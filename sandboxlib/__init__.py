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


'''sandboxlib module.

This module contains multiple 'executor' backends, which must all provide
the same API. A stub version of the API is defined in this file, with
docstrings that describe the different parameters.

'''


import logging
import platform
import shutil
import subprocess
import sys


def maximum_possible_isolation():
    '''Describe the 'tightest' isolation possible with a specific backend.

    This function returns a dict, with the following keys:

      - mounts
      - network

    Each key maps to a parameter of the run_sandbox() function, and each
    value is a valid value for that parameter.

    Example result:

        {
            'mounts': 'undefined'
            'network': 'isolated'
        }

    You can pass the result directly to a run_sandbox() function directly,
    using the `**` operator to turn it into keyword arguments as in the
    following example:

        isolation_settings = maximum_possible_isolation()
        run_sandbox(root_path, ['echo', 'hello'], **isolation_settings)

    '''
    raise NotImplementedError()


def run_sandbox(rootfs_path, command, cwd=None, extra_env=None,
                network='undefined'):
    '''Run 'command' in a sandboxed environment.

    Parameters:
      - command: the command to run. Pass a list of parameters rather than
            using spaces to separate them, e.g. ['echo', '"Hello world"'].
      - cwd: the working directory of 'command', relative to 'rootfs_path'.
            Defaults to '/' if "rootfs_path" is specified, and the current
            directory of the calling process otherwise.
      - extra_env: environment variables to set in addition to
            BASE_ENVIRONMENT.
      - filesystem_root: the path to the root of the sandbox. Defaults to '/',
            which doesn't isolate the command from the host filesystem at all.
      - filesystem_writable_paths: defaults to 'all', which allows the command
            to write to anywhere under 'filesystem_root' that the user of the
            calling process could write to. Backends may accept a list of paths
            instead of 'all', and will prevent writes to any files not under a
            path in that whitelist. If 'none' or an empty list is passed, the
            whole file-system will be read-only. The paths should be relative
            to filesystem_root. This will processed /after/ extra_mounts are
            mounted.
      - mounts: configures mount sharing. Defaults to 'undefined', where no
            no attempt is made to isolate mounts. Backends may support
            'isolated' as well.
      - extra_mounts: a list of locations to mount inside 'rootfs_path', with
            type and options specified in a backend-specific way.
      - network: configures network sharing. Defaults to 'undefined', where
            no attempt is made to either prevent or provide networking
            inside the sandbox. Backends may support 'isolated' and/or other
            values as well.

    '''
    raise NotImplementedError()


def Popen(command, stderr=None, stdout=None, **sandbox_config):
    '''Start a subprocess in a sandbox and return straight away.

    This function aims to function like subprocess.Popen(), but with the
    subprocess running inside a sandbox. It returns a subprocess.Popen
    instance as soon as 'command' starts executing.

    The 'stderr' and 'stdout' parameters accept None, a file-like object, a
    file descriptor (integer), or subprocess.PIPE. The only difference from
    the subprocess.Popen() function is that 'None' means 'ignore all output'
    rather than 'inherit parent's stdout': if you want to forward output from
    the subprocess to stdout, you must pass `stdout=sys.stdout`.

    The sandbox_config arguments are the same as the run_command() function. In
    most cases you should use run_command() instead of this, but there are
    certain cases where Popen() could be useful. The run_command() function
    buffers all data from stdout and stderr of the subprocess in memory, which
    is impractical if there is a huge amount of data.

    '''
    raise NotImplementedError()


def find_program(program_name):
    # Python 3.3 and newer provide a 'find program in PATH' function. Otherwise
    # we fall back to the `which` program.
    if sys.version_info.major >= 3 and sys.version_info.minor >= 3:
        program_path = shutil.which(program_name)
    else:
        try:
            argv = ['which', program_name]
            program_path = subprocess.check_output(argv).strip()
        except subprocess.CalledProcessError as e:
            logging.debug("Error searching for %s: %s", program_name, e)
            program_path = None
    return program_path


def sandbox_module_for_platform():
    '''Returns an execution module that will work on the current platform.'''

    log = logging.getLogger("sandboxlib")

    backend = None

    if platform.uname() == 'Linux':
        log.info("Linux detected, looking for 'linux-user-chroot'.")
        linux_user_chroot_program = find_program('linux-user-chroot')
        if linux_user_chroot_program is not None:
            log.info("Found %s, choosing 'linux_user_chroot' module.",
                     linux_user_chroot_program)
            backend = sandboxlib.linux_user_chroot
        else:
            log.debug("Did not find 'linux-user-chroot' program in PATH.")

    if backend is None:
        log.info("Choosing 'chroot' sandbox module.")
        backend = sandboxlib.chroot

    return backend


BASE_ENVIRONMENT = {
    # Mandated by https://github.com/appc/spec/blob/master/SPEC.md#execution-environment
    'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
}


def environment_vars(extra_env=None):
    '''Return the complete set of environment variables for a sandbox.

    The base environment is defined above, and callers can add extra variables
    to this or override the defaults by passing a dict to 'extra_env'.

    '''
    env = BASE_ENVIRONMENT.copy()

    if extra_env is not None:
        env.update(extra_env)

    return env


def validate_extra_mounts(extra_mounts):
    '''Validate and fill in default values for 'extra_mounts' setting.'''
    if extra_mounts == None:
        return []

    new_extra_mounts = []

    for mount_entry in extra_mounts:
        if len(mount_entry) == 3:
            new_mount_entry = list(mount_entry) + ['']
        elif len(mount_entry) == 4:
            new_mount_entry = list(mount_entry)
        else:
            raise AssertionError(
                "Invalid mount entry in 'extra_mounts': %s" % mount_entry)

        if new_mount_entry[0] is None:
            new_mount_entry[0] = ''
            #new_mount_entry[0] = 'none'
        if new_mount_entry[2] is None:
            new_mount_entry[2] = ''
        if new_mount_entry[3] is None:
            new_mount_entry[3] = ''
        new_extra_mounts.append(new_mount_entry)

    return new_extra_mounts


def _run_command(argv, cwd=None, env=None, preexec_fn=None):
    '''Wrapper around subprocess.Popen() with common settings.

    This function blocks until the subprocesses has terminated. It then
    returns a tuple of (exit code, stdout output, stderr output).

    '''
    process = subprocess.Popen(
        argv,
        # The default is to share file descriptors from the parent process
        # to the subprocess, which is rarely good for sandboxing.
        close_fds=True,
        cwd=cwd,
        env=env,
        preexec_fn=preexec_fn,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    process.wait()
    return process.returncode, process.stdout.read(), process.stderr.read()


# Executors
import sandboxlib.chroot
import sandboxlib.linux_user_chroot

import sandboxlib.load
