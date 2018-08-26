#!/usr/bin/env python3

'''Push CE test S/W to devices.'''

import ipaddress
import logging
from pathlib import Path
import shlex
import shutil
import socketserver
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import monotonic as now

import loadsdir
import loadsfile
import loadsutil


USAGE = '''
    %(prog)s --list-targets
    %(prog)s [-t] <target> <destination> [opts...]
'''

DEFAULT_SSH = 'ssh'
INSTALLIMAGE = '/sbin/installimage'

TARGETS = {
    'asterix': {
        'desc': 'Asterix complete image',
    },
    'asterix.apps': {
        'desc': 'Asterix arm-a8 application code',
        'subtarget': '/asterix/a8/apps',
        'destpath': '/mnt/base/active/apps.img',
        'posthook': '/bin/mainrestart update',
    },
    'asterix.gui': {
        'desc': 'Asterix GUI code',
        'destpath': '/mnt/base/active/gui.img.tmp',
        'posthook': '''\
            /etc/init.d/S13gui unmount_img > /dev/null 2>&1 &&
            mv "$destpath" "${destpath%.tmp}" &&
            /etc/init.d/S13gui mount_img > /dev/null 2>&1''',
    },
    'asterix.prodtest': {
        'desc': 'Asterix prodtest image',
    },
    'asterix.slaveapps': {
        'desc': 'Asterix application code for slave processors, '
                'target arm-m3 and c674x, sysbios',
        'subtarget': '/asterix/slaveapps',
        'destpath': '/mnt/base/active/slaveapps.img',
        'posthook': '/bin/mainrestart update',
    },
    'barents': {
        'desc': 'Barents image',
    },
    'barents.prodtest': {
        'desc': 'Barents prodtest image',
    },
    'carbon': {
        'desc': 'Carbon complete image, target tilera linux',
    },
    'carbon.gui': {
        'desc': 'Carbon GUI code, target tilera linux',
        'destpath': '/mnt/base/active/fs/gui.img.tmp',
        'posthook': '''\
            systemctl stop run-mnt-gui.mount &&
            mv "$destpath" "${destpath%.tmp}" &&
            systemctl start gui.service''',
    },
    'carbon.prodtest': {
        'desc': 'Carbon prodtest image, target tilera linux',
    },
    'ce-host': {
        'desc': 'CE Host VM image, target x86 linux',
        'ssh': 'vm_ssh',
    },
    'drishti': {
        'desc': 'Drishti complete image',
    },
    'drishti.apps': {
        'desc': 'Drishti arm-a9 application code',
        'subtarget': '/drishti/a9/apps',
        'destpath': '/mnt/base/active/apps.img',
    },
    'drishti.gui': {
        'desc': 'Drishti GUI code',
        'destpath': '/mnt/base/active/gui.img.tmp',
        'posthook': '''\
            /etc/init.d/S13gui unmount_img > /dev/null 2>&1 &&
            mv "$destpath" "${destpath%.tmp}" &&
            /etc/init.d/S13gui mount_img > /dev/null 2>&1''',
    },
    'drishti.prodtest': {
        'desc': 'Drishti prodtest image',
    },
    'drishti.qml2': {
        'desc': 'Drishti + QML2 complete image',
    },
    'halley': {
        'desc': 'Halley complete image',
    },
    'halley.apps': {
        'desc': 'Halley arm application code',
        'subtarget': '/halley/arm/apps',
    },
    'idefix': {
        'desc': 'Idefix complete image',
    },
    'moody': {
        'desc': 'Moody complete image',
    },
    'pyramid': {
        'desc': 'Pyramid complete image',
    },
    'pyramid.prodtest': {
        'desc': 'Pyramid prodtest image',
    },
    'sunrise': {
        'desc': 'Sunrise complete image',
        'prefer_loads': True,
    },
    'sunrise.gui': {
        'desc': 'Sunrise GUI code',
        'destpath': '/mnt/base/active/fs/gui.img.tmp',
        'posthook': '''\
            touch /mnt/base/active/pkg.modified &&
            systemctl stop run-mnt-gui.mount &&
            mv "$destpath" "${destpath%.tmp}" &&
            systemctl start gui.service''',
    },
    'sunrise.prodtest': {
        'desc': 'Sunrise prodtest image',
    },
    'sunrise.r28n': {
        'desc': 'Sunrise r28n complete image',
        'prefer_loads': True,
        'loadsname': 'sunrise',
    },
    'sunrise.r28n.gui': {
        'desc': 'Sunrise r28n GUI code',
        'destpath': '/mnt/base/active/fs/gui.img.tmp',
        'posthook': '''\
            touch /mnt/base/active/pkg.modified &&
            systemctl stop run-mnt-gui.mount &&
            mv "$destpath" "${destpath%.tmp}" &&
            systemctl start gui.service''',
    },
    'sunrise.r28n.prodtest': {
        'desc': 'Sunrise r28n prodtest image',
    },
    'tempo': {
        'desc': 'Tempo complete image',
    },
    'zenith': {
        'desc': 'Zenith complete image',
        'prefer_loads': True,
    },
    'zenith.gui': {
        'desc': 'Zenith GUI code',
        'destpath': '/mnt/base/active/fs/gui.img.tmp',
        'posthook': '''\
            touch /mnt/base/active/pkg.modified &&
            systemctl stop run-mnt-gui.mount &&
            mv "$destpath" "${destpath%.tmp}" &&
            systemctl start gui.service''',
    },
    'zenith.prodtest': {
        'desc': 'Zenith prodtest image',
    },
}


class BinstTarget:
    @classmethod
    def create(cls, name):
        assert name in TARGETS
        return cls(name=name, **TARGETS[name])

    def __init__(self, name, *, desc, subtarget=None, ssh=None, destpath=None,
                 posthook=None, prefer_loads=False, loadsname=None):
        self.name = name
        self.description = desc
        self.subtarget = subtarget
        self.ssh = DEFAULT_SSH if ssh is None else ssh
        self.destpath = destpath
        self.posthook = posthook
        self.prefer_loads = prefer_loads
        self.loadsname = self.name if loadsname is None else loadsname
        if self.prefer_loads:
            assert self.support_loads(), (
                self.name + ' prefers --loads, but does not support it!')

    def support_loads(self):
        return self.loadsname in loadsfile.Targets

    def find_image(self, objdir=None):
        '''Return path to image for this build target.'''
        return loadsdir.find_pkg(self.name, objdir)

    def is_remotesupport_compatible(self):
        # remotesupport user is limited to a shell where only a few commands
        # are allowed via sudo. This includes 'installimage' which is used to
        # install most of the "complete" targets, but does NOT include things
        # like 'cat' or 'dd' (or scp for that matter) which we would need in
        # order to place the image at self.destpath.
        return self.destpath is None

    def remote_script(self, allow_test_sw=False, sudo='', install_args=''):
        '''Prepare the shell commands to run over SSH on the remote device.

        The script returned from here may expect the target image to be
        streamed into its stdin.
        '''
        script = ['. /etc/profile']
        if allow_test_sw:
            script.append('touch /tmp/allow_test_software')

        if self.destpath is None:  # pass image directly into installimage
            script.append('{} {} -k /mnt/base/active/rk -f - {}'.format(
                sudo, INSTALLIMAGE, install_args))
        else:  # store image at self.destpath
            script.extend([
                'destpath={}'.format(self.destpath),
                'cat - >"$destpath.tmp" && mv "$destpath.tmp" "$destpath"',
            ])

        if self.posthook is not None:
            script.append(self.posthook)

        return '; '.join(script)


def ssh_address(address):
    '''Format IPv6 addresses to be compatible with SSH command line.'''
    try:
        addr = ipaddress.IPv6Address(address)
        # For link-local addresses that do not already specify the appropriate
        # network interface, we need to guess the correct interface.
        if addr.is_link_local:
            address += '%{}'.format(loadsutil.ip_route(address)['dev'])
    except Exception:  # Not IPv6 address, or already ends with '%{interface}'
        pass

    return address


def build_ssh_cmd(user, destination, remote_cmd, *, ssh='ssh'):
    opts = '-o "StrictHostKeyChecking=no" -o "UserKnownHostsFile=/dev/null"'
    return '{} {} {}@{} {}'.format(
        ssh, opts, user, destination, shlex.quote(remote_cmd))


class LoadsServer:
    @staticmethod
    def _prepare_loadsdir(where, target, target_pkg, objdir):
        if target_pkg == Path('-'):  # PKG on stdin
            target_pkg = where / 'target.pkg'
            with target_pkg.open('wb') as f:
                shutil.copyfileobj(sys.stdin.buffer, f)

        try:
            loads_path = loadsdir.build_with_deps(
                where, target, pkg=target_pkg, objdir=objdir)
        except RuntimeError as e:
            print(e.args)
            print('Either rerun with --no-loads or build these targets first!')
            sys.exit(1)

        return loads_path.relative_to(where)

    # Use ForkingTCPServer to serve multiple requests simultaneously
    class BinstServer(socketserver.ForkingTCPServer):
        def __init__(self, *args, **kwargs):
            self.has_timed_out = False
            super().__init__(*args, **kwargs)

        def handle_timeout(self):
            self.has_timed_out = True

    def __init__(self, binst_target, target_pkg, objdir):
        self._tmpdir = TemporaryDirectory()
        self.loadsdir = Path(self._tmpdir.name)
        loads_target = loadsfile.Targets[binst_target.loadsname]
        self.loadspath = self._prepare_loadsdir(
            self.loadsdir, loads_target, target_pkg, objdir)

        # Setup a simple HTTP server to serve files from self.loadsdir.
        self.server = loadsdir.http_server(
            self.loadsdir, Server=self.BinstServer)
        self.port = self.server.server_address[1]

    def serve(self):
        print('Serving loads upgrade from {} over port {}...'.format(
            self.loadsdir, self.port))
        print('Press Ctrl+C to abort at any time')
        print('Waiting for up to 5 seconds for first request...')
        self.server.timeout = 5
        self.server.handle_request()
        if self.server.has_timed_out:
            print('No incoming requests. Aborting.')
            self.cleanup()
            return False
        else:
            print('Incoming request. Will quit 30s after the last request.')
            self.server.timeout = 30
            start = now()
            while now() - start < self.server.timeout:
                start = now()
                self.server.handle_request()
            self.cleanup()
            return True

    def cleanup(self):
        self.server.server_close()
        self._tmpdir.cleanup()

    def __del__(self):
        self.cleanup()


def parse_args(*args):
    from argparse import ArgumentParser, ArgumentTypeError, Action, SUPPRESS

    class ListTargets(Action):
        def __init__(self, default=SUPPRESS, **kwargs):
            super().__init__(nargs=0, default=default, **kwargs)

        def __call__(self, parser, *args):
            print('\n'.join(sorted(TARGETS.keys())))
            parser.exit()

    def parse_target(name):
        if name not in TARGETS:
            raise ArgumentTypeError(
                'Invalid target "{}", must be one of:\n    {}'.format(
                    name, '\n    '.join(sorted(TARGETS.keys()))))
        return BinstTarget.create(name)

    parser = ArgumentParser(
        usage=USAGE, description=sys.modules[__name__].__doc__)

    parser.add_argument(
        '--list-targets', '--tlist', action=ListTargets,
        help='List available targets.')

    # Allow positional <target> to alternatively be specified with -t/--target
    target_spec = parser.add_mutually_exclusive_group(required=True)
    target_spec.add_argument(
        'target', nargs='?', type=parse_target,
        metavar='<target>', help='Install image for this build target.')
    target_spec.add_argument(
        '--target', '-t', dest='target_alt', type=parse_target,
        metavar='<target>', help='Install image for this build target.')

    parser.add_argument(
        'destination',
        metavar='<destination>', help='Hostname or IP address of device.')

    parser.add_argument(
        '--loads', '-l', action='store_true', default=None,
        help='Upgrade via .loads file (includes peripherals for sunrise/zenith).')
    parser.add_argument(
        '--no-loads', dest='loads', action='store_false',
        help='Upgrade via .pkg file (EXCLUDES peripherals for sunrise/zenith).')
    parser.add_argument(
        '--objdir', '-O',
        help='Pick install file from this path.')
    parser.add_argument(
        '--file', '-f', type=Path,
        help='Install a specific image.')
    parser.add_argument(
        '--verbose', '-v', action='store_true',
        help='Show verbose file transfer information. Uses pv(1)')
    parser.add_argument(
        '--allow-test-software', '-y', action='store_true',
        help='Allow installing test S/W on top of release S/W.')
    parser.add_argument(
        '--unprod', '-u', action='store_true',
        help='Move from release S/W using remotesupport user.')
    parser.add_argument(
        '--install-args', '-e',
        help='Extra arguments to installimage on device.')
    parser.add_argument(
        '--via',
        help='Install via another host.')

    args = parser.parse_args(args)

    # Clean up <target> positional arg vs. -t/--target value
    if args.target_alt and not args.target:
        args.target = args.target_alt
    delattr(args, 'target_alt')

    if args.unprod and not args.target.is_remotesupport_compatible():
        parser.error('''
Cannot combine -u/--unprod with target {}!
Target does not support installation with sudo, root access is necessary.
'''.format(args.target.name))

    if args.loads is None and args.target.prefer_loads:
        args.loads = True
    if args.loads and args.via:
        parser.error('Cannot combine loads upgrade with --via!')

    return args


def main(*args):
    logging.basicConfig(level=logging.INFO)
    args = parse_args(*args)

    print('Installing {}'.format(args.target.name))
    print(args.target.description)

    print('Determining local image path...')
    image_path = args.file if args.file else args.target.find_image(args.objdir)
    if not image_path.exists() and image_path != Path('-'):
        print('Cannot find {} image at {}'.format(args.target.name, image_path))
        return 2

    print('File: {}'.format(image_path))
    print('Destination: {}'.format(args.destination))
    if args.via:
        print('Via: {}'.format(args.via))

    if args.unprod:
        print('''
To go from production SW to test SW you need to create a "remotesupport" user.
This can be done at
    http://{}/web/system-recovery/remotesupportuser
or by using
    xcommand UserManagement RemoteSupportUser Create ExpiryDays: <1..31>
at the tsh and then decoding the phrase at https://rst.cisco.com
'''.format(args.destination))
        input('Do so now and hit enter when ready...')
        remote_user = 'remotesupport'
        args.allow_test_software = True
        sudo = 'sudo'
    else:
        remote_user = 'root'
        sudo = ''

    if args.loads:
        assert args.target.support_loads()
        server = LoadsServer(args.target, image_path, args.objdir)
        script = '; '.join([
            'origin=$(echo $SSH_CLIENT | cut -d" " -f1)',
            'upgrade_url="http://$origin:{0.port}/{0.loadspath}"'.format(server),
            'echo "xcom SystemUnit SoftwareUpgrade URL: $upgrade_url" | tsh',
        ])
        ssh_cmd = build_ssh_cmd(
            remote_user,
            ssh_address(args.destination),
            script,
            ssh=args.target.ssh)
        print('Triggering {} to upgrade from our port {}...'.format(
            args.destination, server.port))
        if args.verbose:
            print('Running: {}'.format(ssh_cmd))
        if subprocess.call(ssh_cmd, shell=True) != 0:
            print('Failed to trigger upgrade (command: {}).'.format(ssh_cmd))
        else:  # Hand control over to loads server.
            if server.serve():  # Files were served to destination. We're done.
                return 0
            else:  # Destination failed to request anything from us.
                print('No upgrade requests from {}!'.format(args.destination))
        server.cleanup()
        print('Falling back to old/--no-loads behavior...')

    script = args.target.remote_script(
        args.allow_test_software, sudo, args.install_args or '')
    ssh_cmd = build_ssh_cmd(
        remote_user, ssh_address(args.destination), script, ssh=args.target.ssh)

    if args.via:
        ssh_cmd = build_ssh_cmd(remote_user, ssh_address(args.via), ssh_cmd)

    cat_cmd = 'cat'
    if args.verbose:
        if shutil.which('pv') is None:
            print('WARNING: Install pv(1) to get file transfer progress!')
        else:
            cat_cmd = 'pv'
            if image_path != Path('-'):
                cat_cmd += ' --size={}'.format(image_path.stat().st_size)

    cmd = '{} {} | {}'.format(cat_cmd, image_path, ssh_cmd)
    if args.verbose:
        print('Running: {}'.format(ssh_cmd))

    print('Connecting...')
    subprocess.call(cmd, shell=True)

    return 0


if __name__ == '__main__':
    sys.exit(main(*sys.argv[1:]))
