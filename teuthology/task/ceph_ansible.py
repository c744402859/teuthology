import json
import os
import re
import logging
import yaml

from cStringIO import StringIO

from . import Task
from tempfile import NamedTemporaryFile
from ..config import config as teuth_config
from ..misc import get_scratch_devices
from teuthology import contextutil
from teuthology.orchestra import run
from teuthology import misc as teuthology
log = logging.getLogger(__name__)


class CephAnsible(Task):
    name = 'ceph_ansible'

    _default_playbook = [
        dict(
            hosts='mons',
            become=True,
            roles=['ceph-mon'],
        ),
        dict(
            hosts='osds',
            become=True,
            roles=['ceph-osd'],
        ),
        dict(
            hosts='mdss',
            become=True,
            roles=['ceph-mds'],
        ),
        dict(
            hosts='rgws',
            become=True,
            roles=['ceph-rgw'],
        ),
        dict(
            hosts='clients',
            become=True,
            roles=['ceph-client'],
        ),
        dict(
            hosts='restapis',
            become=True,
            roles=['ceph-restapi'],
        ),
    ]

    __doc__ = """
    A subclass of Task that defaults to:

    - ansible:
        repo: {git_base}ceph-ansible.git
        branch: mybranch # defaults to master
        playbook: {playbook}

    It always uses a dynamic inventory.

    It will optionally do the following automatically based on ``vars`` that
    are passed in:
        * Set ``devices`` for each host if ``osd_auto_discovery`` is not True
        * Set ``monitor_interface`` for each host if ``monitor_interface`` is
          unset
        * Set ``public_network`` for each host if ``public_network`` is unset
    """.format(
        git_base=teuth_config.ceph_git_base_url,
        playbook=_default_playbook,
    )

    def __init__(self, ctx, config):
        super(CephAnsible, self).__init__(ctx, config)
        config = config or dict()
        if 'playbook' not in config:
            self.playbook = self._default_playbook
        else:
	    self.playbook = self.config['playbook']
        if 'repo' not in config:
            self.config['repo'] = os.path.join(teuth_config.ceph_git_base_url,
                                               'ceph-ansible.git')

    def setup(self):
        super(CephAnsible, self).setup()
        # generate hosts file based on test config
        self.generate_hosts_file()
        # use default or user provided playbook file
        pb_buffer = StringIO()
        pb_buffer.write('---\n')
        yaml.safe_dump(self.playbook, pb_buffer)
        pb_buffer.seek(0)
        playbook_file = NamedTemporaryFile(
            prefix="ceph_ansible_playbook_",
            dir='/tmp/',
            delete=False,
        )
        playbook_file.write(pb_buffer.read())
        playbook_file.flush()
        self.playbook_file = playbook_file

    def execute_playbook(self, _logfile=None):
        """
        Execute ansible-playbook

        :param _logfile: Use this file-like object instead of a LoggerFile for
                         testing
        """

        # everything from vars in config go into extra-vars
        # TODO add option to directly copy them to group_vars/all
        extra_vars = dict()
        extra_vars.update(self.config.get('vars', dict()))
        args = [
            'ansible-playbook', '-v',
            "--extra-vars", "'%s'" % json.dumps(extra_vars),
            '-i', self.inventory,
            self.playbook_file.name,
        ]
        log.debug("Running %s", args)
        # use the first mon node as installer node
        (ceph_installer,) = self.ctx.cluster.only(
            teuthology.get_first_mon(self.ctx,
                                     self.config)).remotes.iterkeys()
        self.installer_node = ceph_installer
        if self.config.get('rhbuild'):
            ceph_installer.put_file(args[5], '/tmp/inven.yml')
            ceph_installer.put_file(args[6], '/tmp/site.yml')
            ceph_installer.run(args=['cp', '-R',
                                     '/usr/share/ceph-ansible', '.'])
            ceph_installer.run(args=('cat', args[6], run.Raw('>'),
                                     'ceph-ansible/site.yml'))
            ceph_installer.run(args=('cat', args[5]))
            ceph_installer.run(args=('cat', 'ceph-ansible/site.yml'))
            if self.config.get('group_vars'):
                self.set_groupvars(ceph_installer)
            args[6] = 'site.yml'
            out = StringIO()
            str_args = ' '.join(args)
            ceph_installer.run(args=['cd', 'ceph-ansible', run.Raw(';'),
                                     run.Raw(str_args)],
                               timeout=4200,
                               check_status=False,
                               stdout=out)
            log.info(out.getvalue())
            if re.search(r'all hosts have already failed', out.getvalue()):
                log.error("Failed during ansible execution")
                raise CephAnsibleError("Failed during ansible execution")
            self.setup_client_node()
        else:
            # super(CephAnsible, self).execute_playbook()
            # setup ansible on first mon node
            # use ansible < 2.0
            if ceph_installer.os.package_type == 'rpm':
                # install crypto packages for ansible
                ceph_installer.run(args=['sudo', 'yum',
                                         'install',
                                         '-y',
                                         'libffi-devel',
                                         'python-devel',
                                         'openssl-devel'])
            else:
                ceph_installer.run(args=['sudo', 'apt-get',
                                         'install',
                                         '-y',
                                         'libssl-dev',
                                         'libffi-dev',
                                         'python-dev'])
            ansible_repo = self.config['repo']
            branch = '-b master'
            if self.config.get('branch'):
                branch = ' -b ' + self.config.get('branch')
            ceph_installer.run(args=['rm', '-rf',
			             run.Raw('~/ceph-ansible'),
                                    ], check_status=False)
            ceph_installer.run(args=['mkdir',
                                     run.Raw('~/ceph-ansible'),
                                     run.Raw(';'),
                                     'git',
                                     'clone',
                                     run.Raw(branch),
                                     run.Raw(ansible_repo), ])
            # copy the inventory file to installer node
            ceph_installer.put_file(args[5], './ceph-ansible/inven.yml')
            # copy the site file
            ceph_installer.put_file(args[6], './ceph-ansible/site.yml')
            args[5] = 'inven.yml'
            args[6] = 'site.yml'
            out = StringIO()
            str_args = ' '.join(args)
            ceph_installer.run(args=[run.Raw('cd ~/ceph-ansible'),
                                     run.Raw(';'),
                                     'virtualenv',
                                     '--system-site-packages',
                                     'venv',
                                     run.Raw(';'),
                                     run.Raw('source venv/bin/activate'),
                                     run.Raw(';'),
                                     'pip',
                                     'install',
                                     'ansible==1.9.4',
                                     run.Raw(';'),
                                     run.Raw(str_args)
                                     ])
        wait_for_health = self.config.get('wait-for-health', True)
        if wait_for_health:
            self.wait_for_ceph_health()

    def generate_hosts_file(self):
        groups_to_roles = dict(
            mons='mon',
            mdss='mds',
            osds='osd',
            clients='client',
        )
        hosts_dict = dict()
        for group in sorted(groups_to_roles.keys()):
            role_prefix = groups_to_roles[group]
            want = lambda role: role.startswith(role_prefix)
            for (remote, roles) in self.cluster.only(want).remotes.iteritems():
                hostname = remote.hostname
                host_vars = self.get_host_vars(remote)
                if group not in hosts_dict:
                    hosts_dict[group] = {hostname: host_vars}
                elif hostname not in hosts_dict[group]:
                    hosts_dict[group][hostname] = host_vars

        hosts_stringio = StringIO()
        for group in sorted(hosts_dict.keys()):
            hosts_stringio.write('[%s]\n' % group)
            for hostname in sorted(hosts_dict[group].keys()):
                vars = hosts_dict[group][hostname]
                if vars:
                    vars_list = []
                    for key in sorted(vars.keys()):
                        vars_list.append(
                            "%s='%s'" % (key, json.dumps(vars[key]).strip('"'))
                        )
                    host_line = "{hostname} {vars}".format(
                        hostname=hostname,
                        vars=' '.join(vars_list),
                    )
                else:
                    host_line = hostname
                hosts_stringio.write('%s\n' % host_line)
            hosts_stringio.write('\n')
        hosts_stringio.seek(0)
        self.inventory = self._write_hosts_file(hosts_stringio.read().strip())
        self.generated_inventory = True

    def begin(self):
        super(CephAnsible, self).begin()
        self.execute_playbook()

    def _write_hosts_file(self, content):
        """
        Actually write the hosts file
        """
        hosts_file = NamedTemporaryFile(prefix="teuth_ansible_hosts_",
                                        delete=False)
        hosts_file.write(content)
        hosts_file.flush()
        return hosts_file.name

    def wait_for_ceph_health(self):
        with contextutil.safe_while(sleep=15, tries=6,
                                    action='check health') as proceed:
            (remote,) = self.ctx.cluster.only('mon.a').remotes
            remote.run(args=['sudo', 'ceph', 'osd', 'tree'])
            remote.run(args=['sudo', 'ceph', '-s'])
            log.info("Waiting for Ceph health to reach HEALTH_OK \
                        or HEALTH WARN")
            while proceed():
                out = StringIO()
                remote.run(args=['sudo', 'ceph', 'health'], stdout=out)
                out = out.getvalue().split(None, 1)[0]
                log.info("cluster in state: %s", out)
                if (out == 'HEALTH_OK' or out == 'HEALTH_WARN'):
                    break

    def get_host_vars(self, remote):
        extra_vars = self.config.get('vars', dict())
        host_vars = dict()
        if not extra_vars.get('osd_auto_discovery', False):
            roles = self.ctx.cluster.remotes[remote]
            dev_needed = len([role for role in roles
                              if role.startswith('osd')])
            host_vars['devices'] = get_scratch_devices(remote)[0:dev_needed]
        if 'monitor_interface' not in extra_vars:
            host_vars['monitor_interface'] = remote.interface
        if 'public_network' not in extra_vars:
            host_vars['public_network'] = remote.cidr
        return host_vars


class CephAnsibleError(Exception):
    pass

task = CephAnsible
