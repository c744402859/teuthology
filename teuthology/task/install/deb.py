import logging
import os

from cStringIO import StringIO

from teuthology.orchestra import run

from . import util


log = logging.getLogger(__name__)


def _update_package_list_and_install(ctx, remote, debs, config):
    """
    Runs ``apt-get update`` first, then runs ``apt-get install``, installing
    the requested packages on the remote system.

    TODO: split this into at least two functions.

    :param ctx: the argparse.Namespace object
    :param remote: the teuthology.orchestra.remote.Remote object
    :param debs: list of packages names to install
    :param config: the config dict
    """

    # check for ceph release key
    r = remote.run(
        args=[
            'sudo', 'apt-key', 'list', run.Raw('|'), 'grep', 'Ceph',
        ],
        stdout=StringIO(),
        check_status=False,
    )
    if r.stdout.getvalue().find('Ceph automated package') == -1:
        # if it doesn't exist, add it
        remote.run(
            args=[
                'wget', '-q', '-O-',
                'http://git.ceph.com/?p=ceph.git;a=blob_plain;f=keys/autobuild.asc',  # noqa
                run.Raw('|'),
                'sudo', 'apt-key', 'add', '-',
            ],
            stdout=StringIO(),
        )

    builder = util._get_builder_project(ctx, remote, config)
    log.info("Installing packages: {pkglist} on remote deb {arch}".format(
        pkglist=", ".join(debs), arch=builder.arch)
    )
    # get baseurl
    log.info('Pulling from %s', builder.base_url)

    version = builder.version
    log.info('Package version is %s', version)

    remote.run(
        args=[
            'echo', 'deb', builder.base_url, builder.codename, 'main',
            run.Raw('|'),
            'sudo', 'tee', '/etc/apt/sources.list.d/{proj}.list'.format(
                proj=config.get('project', 'ceph')),
        ],
        stdout=StringIO(),
    )
    remote.run(args=['sudo', 'apt-get', 'update'], check_status=False)
    remote.run(
        args=[
            'sudo', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', '-y',
            '--force-yes',
            '-o', run.Raw('Dpkg::Options::="--force-confdef"'), '-o', run.Raw(
                'Dpkg::Options::="--force-confold"'),
            'install',
        ] + ['%s=%s' % (d, version) for d in debs],
    )
    ldir = util._get_local_dir(config, remote)
    if ldir:
        for fyle in os.listdir(ldir):
            fname = "%s/%s" % (ldir, fyle)
            remote.run(args=['sudo', 'dpkg', '-i', fname],)


def _remove_deb(ctx, config, remote, debs):
    """
    Removes Debian packages from remote, rudely

    TODO: be less rude (e.g. using --force-yes)

    :param ctx: the argparse.Namespace object
    :param config: the config dict
    :param remote: the teuthology.orchestra.remote.Remote object
    :param debs: list of packages names to install
    """
    log.info("Removing packages: {pkglist} on Debian system.".format(
        pkglist=", ".join(debs)))
    # first ask nicely
    remote.run(
        args=[
            'for', 'd', 'in',
        ] + debs + [
            run.Raw(';'),
            'do',
            'sudo',
            'DEBIAN_FRONTEND=noninteractive', 'apt-get', '-y', '--force-yes',
            '-o', run.Raw('Dpkg::Options::="--force-confdef"'), '-o', run.Raw(
                'Dpkg::Options::="--force-confold"'), 'purge',
            run.Raw('$d'),
            run.Raw('||'),
            'true',
            run.Raw(';'),
            'done',
        ])
    # mop up anything that is broken
    remote.run(
        args=[
            'dpkg', '-l',
            run.Raw('|'),
            # Any package that is unpacked or half-installed and also requires
            # reinstallation
            'grep', '^.\(U\|H\)R',
            run.Raw('|'),
            'awk', '{print $2}',
            run.Raw('|'),
            'sudo',
            'xargs', '--no-run-if-empty',
            'dpkg', '-P', '--force-remove-reinstreq',
        ])
    # then let apt clean up
    remote.run(
        args=[
            'sudo',
            'DEBIAN_FRONTEND=noninteractive', 'apt-get', '-y', '--force-yes',
            '-o', run.Raw('Dpkg::Options::="--force-confdef"'), '-o', run.Raw(
                'Dpkg::Options::="--force-confold"'),
            'autoremove',
        ],
    )


def _remove_sources_list(remote, proj):
    """
    Removes /etc/apt/sources.list.d/{proj}.list and then runs ``apt-get
    update``.

    :param remote: the teuthology.orchestra.remote.Remote object
    :param proj: the project whose sources.list needs removing
    """
    remote.run(
        args=[
            'sudo', 'rm', '-f', '/etc/apt/sources.list.d/{proj}.list'.format(
                proj=proj),
            run.Raw('&&'),
            'sudo', 'apt-get', 'update',
        ],
        check_status=False,
    )


def _upgrade_packages(ctx, config, remote, debs):
    """
    Upgrade project's packages on remote Debian host
    Before doing so, installs the project's GPG key, writes a sources.list
    file, and runs ``apt-get update``.

    :param ctx: the argparse.Namespace object
    :param config: the config dict
    :param remote: the teuthology.orchestra.remote.Remote object
    :param debs: the Debian packages to be installed
    :param branch: the branch of the project to be used
    """
    # check for ceph release key
    r = remote.run(
        args=[
            'sudo', 'apt-key', 'list', run.Raw('|'), 'grep', 'Ceph',
        ],
        stdout=StringIO(),
        check_status=False,
    )
    if r.stdout.getvalue().find('Ceph automated package') == -1:
        # if it doesn't exist, add it
        remote.run(
            args=[
                'wget', '-q', '-O-',
                'http://git.ceph.com/?p=ceph.git;a=blob_plain;f=keys/autobuild.asc',  # noqa
                run.Raw('|'),
                'sudo', 'apt-key', 'add', '-',
            ],
            stdout=StringIO(),
        )

    builder = util._get_builder_project(ctx, remote, config)
    base_url = builder.base_url
    log.info('Pulling from %s', base_url)

    version = builder.version
    log.info('Package version is %s', version)

    remote.run(
        args=[
            'echo', 'deb', base_url, builder.codename, 'main',
            run.Raw('|'),
            'sudo', 'tee', '/etc/apt/sources.list.d/{proj}.list'.format(
                proj=config.get('project', 'ceph')),
        ],
        stdout=StringIO(),
    )
    remote.run(args=['sudo', 'apt-get', 'update'], check_status=False)
    remote.run(
        args=[
            'sudo',
            'DEBIAN_FRONTEND=noninteractive', 'apt-get', '-y', '--force-yes',
            '-o', run.Raw('Dpkg::Options::="--force-confdef"'), '-o', run.Raw(
                'Dpkg::Options::="--force-confold"'),
            'install',
        ] + ['%s=%s' % (d, version) for d in debs],
    )