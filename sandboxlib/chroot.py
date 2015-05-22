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


'''Execute command in a sandbox, using os.chroot().

The code would be simpler if we just used the 'chroot' program, but it's not
always practical to do that. First, it may not be installed. Second, we can't
set the working directory of the program inside the chroot, unless we assume
that the sandbox contains a shell and we do some hack like running
`/bin/sh -c "cd foo && command"`. It's better to call the kernel directly.

'''


import os
import subprocess
import sys

import sandboxlib


def maximum_possible_isolation():
    return {
        'network': 'undefined'
    }


def process_network_config(network):
    # It'd be possible to implement network isolation on Linux using the
    # clone() syscall. However, I prefer to have the 'chroot' backend behave
    # the same on all platforms, and have separate Linux-specific backends to
    # do Linux-specific stuff.

    assert network == 'undefined', \
        "'%s' is an unsupported value for 'network' in the 'chroot' backend. " \
        "Network sharing cannot be be configured in this backend." % network


def run_sandbox(rootfs_path, command, cwd=None, extra_env=None,
                network='undefined'):
    if type(command) == str:
        command = [command]

    env = sandboxlib.environment_vars(extra_env)

    process_network_config(network)

    pid = os.fork()
    if pid == 0:
        # Child process. It's a bit messy that we create a child process and
        # then a second child process, but it saves duplicating stuff from the
        # 'subprocess' module.

        # FIXME: you gotta be root for this one.
        try:
            try:
                os.chroot(rootfs_path)
            except OSError as e:
                raise RuntimeError("Unable to chroot: %s" % e)

            result = subprocess.call(command, cwd=cwd, env=env)
        except Exception as e:
            print("ERROR: %s" % e)
            result = 255
        finally:
            os._exit(result)
    else:
        # Parent process. Wait for child to exit.
        os.waitpid(pid, 0)
