# Copyright (C) 2015  Codethink Limited
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program.  If not, see <http://www.gnu.org/licenses/>.


'''Execute command in a sandbox, using 'systemd-nspawn'.

This implements an API defined in sandboxlib/__init__.py.

This backend requires the 'systemd-nspawn' program, which is part of the
'systemd' init system for Linux.

The v209 release of systemd the oldest that might have a chance of working, as
the backend uses the `--quiet` flag that was added in that release.

The 'systemd-nspawn' program can only be used by users with 'root' privileges.

'''


import contextlib
import os
import shutil

import sandboxlib


CAPABILITIES = {
    'network': ['isolated', 'shared', 'undefined'],
    'mounts': ['isolated', 'undefined'],
    'filesystem_writable_paths': ['all', 'none'],
}


def degrade_config_for_capabilities(in_config, warn=True):
    # FIXME: not sure if all 'filesystem_writable_paths' features are
    # supported.
    return in_config


def args_for_mount(mount_source, mount_target, mount_type, mount_options,
                   tmpfs_dir):
    def is_none(value):
        return value in [None, 'none', '']

    args = []
    if mount_type == 'proc':
        if not is_none(mount_options):
            raise AssertionError(
                "No options for 'proc' filesystems are supported in the "
                "linux-user-chroot backend. Got '%s'" % mount_options)
        else:
            args = ['--mount-proc', mount_target]
    elif mount_type == 'tmpfs':
        if not is_none(mount_options):
            raise AssertionError(
                "No options for 'tmpfs' filesystems are supported in the "
                "linux-user-chroot backend. Got '%s'" % mount_options)
        else:
            # tmpfs mounts are 'faked' by binding in a temporary directory
            # from a temporary directory in an existing tmpfs.
            fake_tmpfs = os.path.join(tmpfs_dir, mount_target.lstrip('/'))
            os.makedirs(fake_tmpfs)
            args = ['--mount-bind', fake_tmpfs, mount_target]
    elif mount_options == 'bind':
        if not is_none(mount_type):
            raise AssertionError(
                "Type cannot be specified for 'bind' mounts. Got '%s'" %
                mount_type)
        else:
            args = ['--mount-bind', mount_source, mount_target]
    else:
        raise AssertionError(
            "Unsupported mount type '%s' for linux-user-chroot backend." %
            mount_type)

    return args


@contextlib.contextmanager
def process_mount_config(mounts, extra_mounts):
    # linux-user-chroot always calls clone(CLONE_NEWNS) which creates a new
    # mount namespace. It also ensures that all mount points inside the sandbox
    # are private, by calling mount("/", MS_PRIVATE | MS_REC). So 'isolated' is
    # the only option for 'mounts'.

    sandboxlib.utils.check_parameter('mounts', mounts, CAPABILITIES['mounts'])

    # This is only used if there are tmpfs mounts, but it's simpler to
    # create it unconditionally.
    tmpfs_dir = tmpfs_for_user()

    try:
        extra_linux_user_chroot_args = []

        for mount_info in extra_mounts:
            args = args_for_mount(*mount_info, tmpfs_dir=tmpfs_dir)
            extra_linux_user_chroot_args.extend(args)

        yield extra_linux_user_chroot_args
    finally:
        # The tmpfs dir is a directory *in* a pre-existing tmpfs, so we need
        # to delete its contents.
        shutil.rmtree(tmpfs_dir)


def process_network_config(network):
    sandboxlib.utils.check_parameter('network', network, CAPABILITIES['network'])

    if network == 'isolated' or network == 'undefined':
        extra_systemd_nspawn_args = ['--private-network']
    elif network == 'shared':
        # Sharing the host network is the default behaviour of systemd-nspawn.
        extra_systemd_nspawn_args = []

    return extra_systemd_nspawn_args


# This function is mostly taken from Morph, from the Baserock project, from
# file morphlib/fsutils.py.
#
# It is used to convert the whitelist 'filesystem_writable_paths' into a
# blacklist of '--mount-readonly' arguments for linux-user-chroot. It would
# be better if we could pass the whitelist into linux-user-chroot itself,
# all that is needed is a patch to linux-user-chroot.
def invert_paths(tree_walker, paths):
    '''List paths from `tree_walker` that are not in `paths`.

    Given a traversal of a tree and a set of paths separated by os.sep,
    return the files and directories that are not part of the set of
    paths, culling directories that do not need to be recursed into,
    if the traversal supports this.

    `tree_walker` is expected to follow similar behaviour to `os.walk()`.

    This function will remove directores from the ones listed, to avoid
    traversing into these subdirectories, if it doesn't need to.

    As such, if a directory is returned, it is implied that its contents
    are also not in the set of paths.

    If the tree walker does not support culling the traversal this way,
    such as `os.walk(root, topdown=False)`, then the contents will also
    be returned.

    The purpose for this is to list the directories that can be made
    read-only, such that it would leave everything in paths writable.

    Each path in `paths` is expected to begin with the same path as
    yielded by the tree walker.

    '''

    def normpath(path):
        if path == '.':
            return path
        path = os.path.normpath(path)
        if not os.path.isabs(path):
            path = os.path.join('.', path)
        return path
    def any_paths_are_subpath_of(prefix):
        prefix = normpath(prefix)
        norm_paths = (normpath(path) for path in paths)
        return any(path[:len(prefix)] == prefix
                   for path in norm_paths)

    def path_is_listed(path):
        return any(normpath(path) == normpath(other)
                   for other in paths)

    for dirpath, dirnames, filenames in tree_walker:

        if path_is_listed(dirpath):
            # No subpaths need to be considered
            del dirnames[:]
            del filenames[:]
        elif any_paths_are_subpath_of(dirpath):
            # Subpaths may be marked, or may not, need to leave this
            # writable, so don't yield, but we don't cull.
            pass
        else:
            # not listed as a parent or an exact match, needs to be
            # yielded, but we don't need to consider subdirs, so can cull
            yield dirpath
            del dirnames[:]
            del filenames[:]

        for filename in filenames:
            fullpath = os.path.join(dirpath, filename)
            if path_is_listed(fullpath):
                pass
            else:
                yield fullpath


def process_writable_paths(fs_root, writable_paths):
    if writable_paths == 'all':
        extra_linux_user_chroot_args = []
    else:
        if type(writable_paths) != list:
            assert writable_paths in [None, 'none']
            writable_paths = []

        # FIXME: It's rather annoying that we have to convert the
        # 'writable_paths' whitelist into a blacklist of '--mount-readonly'
        # arguments. It's also possible to break here by making a commandline
        # that is too long, if 'fs_root' contains many directories.

        extra_linux_user_chroot_args = []

        absolute_writable_paths = [
            os.path.join(fs_root, path.lstrip('/')) for path in writable_paths]

        for d in invert_paths(os.walk(fs_root), absolute_writable_paths):
            if not os.path.islink(d):
                rel_path = '/' + os.path.relpath(d, fs_root)
                extra_linux_user_chroot_args.extend(
                    ['--mount-readonly', rel_path])

    return extra_linux_user_chroot_args


def create_mount_points_if_missing(filesystem_root, mount_info_list):
    for source, mount_point, mount_type, mount_options in mount_info_list:
        # Strip the preceeding '/' from mount_point, because it'll break
        # os.path.join().
        mount_point_no_slash = os.path.abspath(mount_point).lstrip('/')

        path = os.path.join(filesystem_root, mount_point_no_slash)
        if not os.path.exists(path):
            os.makedirs(path)


def create_etc_os_release_if_missing(filesystem_root):
    '''Create /etc/os-release in the sandbox if it is missing.

    The 'systemd-nspawn' program requires that this file exists. That is
    probably because it was originally intended as a tool for running complete
    operating systems.

    This function creates a dummy /etc/os-release file if there isn't one
    present, which is a bit of a kludge, but allows us to use systemd-nspawn
    for more general-purpose sandboxing.

    '''
    etc_dir = os.path.join(filesystem_root, 'etc')
    os_release_file = os.path.join(etc_dir, 'os-release')

    if not os.path.exists(os_release_file):
        if not os.path.exists(etc_dir):
            os.makedirs(etc_dir)
        with open(os_release_file, 'w') as f:
            f.write("Dummy /etc/os-release file created by 'sandboxlib'.")


def systemd_nspawn_program():
    # Raises sandboxlib.ProgramNotFound if not found.
    return sandboxlib.utils.find_program('systemd-nspawn')


def run_sandbox(command, cwd=None, env=None,
                filesystem_root='/', filesystem_writable_paths='all',
                mounts='undefined', extra_mounts=None,
                network='undefined',
                stdout=sandboxlib.CAPTURE, stderr=sandboxlib.CAPTURE):
    if type(command) == str:
        command = [command]

    systemd_nspawn_command = [systemd_nspawn_program()]

    #extra_mounts = sandboxlib.validate_extra_mounts(extra_mounts)

    systemd_nspawn_command += process_network_config(network)

    assert cwd is None, \
        "systemd-nspawn currently does not support setting the current " \
        "working directory inside the container."

    #systemd_nspawn_command += process_writable_paths(
    #    filesystem_root, filesystem_writable_paths)

    #create_mount_points_if_missing(filesystem_root, extra_mounts)

    #mount_context = process_mount_config(
    #    mounts=mounts, extra_mounts=extra_mounts or [])
    #with mount_context as linux_user_chroot_mount_args:
    #    linux_user_chroot_command.extend(linux_user_chroot_mount_args)

    create_etc_os_release_if_missing(filesystem_root)

    argv = systemd_nspawn_command + ['--quiet', '-D', filesystem_root] + command
    exit, out, err = sandboxlib._run_command(argv, stdout, stderr, env=env)
    return exit, out, err


def run_sandbox_with_redirection(command, **sandbox_config):
    exit, out, err = run_sandbox(command, **sandbox_config)
    # out and err will be None
    return exit
