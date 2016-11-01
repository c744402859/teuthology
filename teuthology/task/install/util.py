import os

from teuthology import misc as teuthology
from teuthology import packaging


def _get_builder_project(ctx, remote, config):
    return packaging.get_builder_project()(
        config.get('project', 'ceph'),
        config,
        remote=remote,
        ctx=ctx
    )


def _get_local_dir(config, remote):
    """
    Extract local directory name from the task lists.
    Copy files over to the remote site.
    """
    ldir = config.get('local', None)
    if ldir:
        remote.run(args=['sudo', 'mkdir', '-p', ldir])
        for fyle in os.listdir(ldir):
            fname = "%s/%s" % (ldir, fyle)
            teuthology.sudo_write_file(
                remote, fname, open(fname).read(), '644')
    return ldir


def get_flavor(config):
    """
    Determine the flavor to use.
    """
    config = config or dict()
    flavor = config.get('flavor', 'basic')

    if config.get('path'):
        # local dir precludes any other flavors
        flavor = 'local'
    else:
        if config.get('valgrind'):
            flavor = 'notcmalloc'
        else:
            if config.get('coverage'):
                flavor = 'gcov'
    return flavor
