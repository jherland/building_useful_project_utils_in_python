#!/usr/bin/env python3
'''Utility for building loads directories around our build products.

loads dirs are directories containing a .loads file and all the PKG files
referenced from the .loads file.

See https://rdwiki.cisco.com/wiki/Swupgrade for more details.
'''

from http.server import SimpleHTTPRequestHandler
import logging
from pathlib import Path
import re
import shutil
from socketserver import ForkingTCPServer
import subprocess
import sys

import loadsfile
import loadssign
import loadsutil


logger = logging.getLogger('loadsdir')

BUILD = loadsutil.MAIN_ROOT / 'build/build'


def version_as_path_fragment(pkg_version):
    '''Convert "ce9.3.0 92f9c9ac866 ..." to "ce9_3_0-92f9c9ac866".'''
    version, commit, rest = pkg_version.split(' ', 2)
    assert re.fullmatch(r'[a-zA-Z]+\d+\.\d+\.\d+', version, re.ASCII)
    assert re.fullmatch(r'[0-9a-fA-F]{11,40}', commit, re.ASCII)
    return '{}-{}'.format(version, commit)


def preferred_pkg_filename(target, pkg_version, suffix='.pkg'):
    '''Return preferred PKG filename for the given target and version.

    This is the PKG filename you'd expect to find inside a COP file for an
    official CE release. For codecs, we use the sNNNNN product name plus the
    PKG version. For peripherals, we use the target name (halley/moody/pyramid)
    plus the PKG version. Examples:
        sunrise: s53200ce9_3_0-92f9c9ac866.pkg
        pyramid: pyramidce9_3_0-92f9c9ac866.pkg
    '''
    prefix = target.product if target.is_codec else target.name
    return prefix + version_as_path_fragment(pkg_version) + suffix


def build(dst, *, targets, pkgs, version=None, filenames=None,
          loads_fname=None, test_signing_key=None, symlink=True):
    '''Store loads file + pkg symlinks for the given 'targets' within 'dst'.

    Write a .loads file inside 'dst' that references the given 'targets' and
    'pkgs' (two lists of corresponding target objects and .pkg paths).
    Also create symlinks (or copies - if 'symlink' is set to False) from
    inside 'dst' to the given pkgs (which may be stored anywhere). The filename
    of the .loads file and the .pkg symlinks/copies can be controlled with the
    'loads_fname' and 'filenames' arguments, respectively. When not given, they
    default to the filename organization that is expected inside a .cop release
    file.
    Return path to the generated .loads file.
    '''
    assert dst.is_dir()
    assert len(targets) == len(pkgs)
    assert all(isinstance(t, loadsfile.Target) for t in targets)
    if version is None:
        version = loadsfile.pkg_info(targets[0], pkgs[0]).version
    if filenames is None:
        filenames = [preferred_pkg_filename(t, version) for t in targets]
    if loads_fname is None:
        loads_fname = preferred_pkg_filename(targets[0], version, '.loads')

    loads = loadsfile.LoadsFile()
    logger.info('Building loads dir at {} from these sources:'.format(dst))
    for target, pkg, fname in zip(targets, pkgs, filenames):
        loads.add(target, pkg, fname)
        logger.info('{:>16}: {:32} -> {}'.format(str(target), fname, pkg))

    # Write loads file
    loads_path = dst / loads_fname
    with loads_path.open('w') as f:
        loads.write(f)

    # Write loads signature
    loads_path_sgn = loads_path.with_suffix('.loads.sgn')
    # TODO: Add release signing
    loadssign.test_sign(loads_path, store=loads_path_sgn, key=test_signing_key)

    # Symlink or copy all PKGs into dst to make them reachable from loads file
    for fname, pkg in zip(filenames, pkgs):
        tgt = dst / fname
        try:
            if symlink:
                tgt.symlink_to(pkg)
            else:
                shutil.copy(pkg, tgt)
        except FileExistsError:
            if not tgt.samefile(pkg):
                raise

    return loads_path


def find_pkg(target_name, objdir=None):
    '''Return path to PKG result for the given build target.'''
    argv = [str(BUILD)]
    if objdir:
        argv += ['--objdir', str(objdir)]
    argv += ['--target', target_name, '--print-target-names', '-Q']
    pkg_path = subprocess.check_output(argv, universal_newlines=True).rstrip()
    assert '\n' not in pkg_path
    return loadsutil.MAIN_ROOT / pkg_path


def find_target_deps_and_pkgs(target, pkg=None, objdir=None):
    '''Find dependencies and their PKG files for the given target.

    This yields (target, pkg_path) tuples for the given 'target' and each of
    its dependencies (according to loadsfile.Targets). PKG files are found by
    passing target.name to find_pkg() (along with 'objdir', if given) for each
    target encountered. The PKG path corresponding to 'target' itself can be
    overridden by passing 'pkg'.
    '''
    assert isinstance(target, loadsfile.Target)
    logger.info('Finding dependencies for {}...'.format(target))

    yield target, find_pkg(target.name, objdir) if pkg is None else pkg

    for dep_name in target.deps:
        assert dep_name in loadsfile.Targets
        yield loadsfile.Targets[dep_name], find_pkg(dep_name, objdir)


def verify_pkgs(targets_and_pkgs):
    '''Verify that the PKGs in the given (target, pkg) tuples exist on disk.

    Pass through all other tuples. Raise ValueError at the end if one or more
    PKGs are missing.
    '''
    missing = []
    for target, pkg in targets_and_pkgs:
        if pkg.is_file():
            yield target, pkg
        else:
            logger.warning("Missing PKG file for {}: {}".format(target, pkg))
            missing.append((target, pkg))

    if missing:
        raise ValueError('Missing targets/PKGs: ' + ', '.join(
            '{}:{}'.format(target, pkg) for target, pkg in missing))


def build_with_deps(dst, target, *, pkg=None, objdir=None, **kwargs):
    '''Build loads dir for the given target and all its dependencies.

    This creates a list of targets from 'target' plus its dependencies (found
    by looking at target.deps), and a corresponding list of PKG files (found by
    calling find_pkg(target.name, objdir) for each of the targets), and passes
    these two lists (along with forwarding 'dst' and any 'kwargs') onto build().

    The result is building a loads directory at 'dst' containing a loads file
    for 'target' and its dependencies.
    '''
    # TODO: Move responsibility for finding PKGs onto the caller. Then, the
    # caller can e.g. ask the endpoint to figure out which PKGs are _actually_
    # required for a successful upgrade (i.e. omit unnecessary peripherals).
    targets, pkgs = zip(
        *verify_pkgs(find_target_deps_and_pkgs(target, pkg, objdir)))
    return build(dst, targets=targets, pkgs=pkgs, **kwargs)


def http_server(loadsdir, address=('', 0), Server=ForkingTCPServer):
    '''Return a HTTP server instance serving the contents of 'loadsdir'.

    The server will be listening on the given address (host, port) tuple.
    If port is 0 (the default), the port number is chosen automatically, and
    can be retrieved from .server_address[1] on the returned server instance.
    '''
    class LoadsRequestHandler(SimpleHTTPRequestHandler):
        server_version = 'loadsdir.py/1'
        extensions_map = {'': 'application/octet-stream'}

        def translate_path(self, path):
            # SimpleHTTPRequestHandler maps to $CWD. Remap to loadsdir
            rel = Path(super().translate_path(path)).relative_to(Path.cwd())
            return str(loadsdir / rel)

        def log_request(self, *args):
            pass  # Silence SimpleHTTPRequestHandler's default request logging

        def do_GET(self):
            """Serve a GET request."""
            # Provide out own request logging instead, that logs both when
            # we start and finish serving files.
            f = self.send_head()
            if f:
                path = Path(f.name).relative_to(loadsdir)
                logger.info('  << Requested: {}...'.format(path))
                try:
                    self.copyfile(f, self.wfile)
                finally:
                    f.close()
                    logger.info('  >> Responded: {}'.format(path))

    ret = Server(address, LoadsRequestHandler)
    logger.info('Serving {} over port {}...'.format(
        loadsdir, ret.server_address[1]))
    return ret


def walk(loadsdir):
    '''Find .loads files under 'loadsdir'.

    For each .loads file found, yield its path and the corresponding
    loadsfile.LoadsFile instance.
    '''
    for path in loadsdir.rglob('*.loads'):
        yield path, loadsfile.LoadsFile.parse(path)


def loads_targets_and_pkgs(loadsdir):
    '''Find .loads files under 'loadsdir' and the targets/pkgs they reference.

    Verify that all PKGs references from .loads files found within 'loadsdir'
    refer to files that also exist within 'loadsdir' (symlinks to files outside
    'loadsdir' are allowed).

    For each .loads file found, yield its path along with a list of (target, pkg_path) entries found in that .loads file. All yielded paths (for both
    .loads and .pkg files) are relative to 'loadsdir'.
    '''
    # Reverse-map product name into target name
    Products = {t.product: t.name for t in loadsfile.Targets.values()}

    for lp, lf in walk(loadsdir):
        def target_and_pkg(entry):
            pkg_ref = Path(entry['packageLocation'])
            assert not pkg_ref.is_absolute()
            pkg_path = lp.parent / pkg_ref
            logger.info('  {} -> {}'.format(pkg_path, pkg_path.resolve()))
            assert pkg_path.resolve().is_file()
            return Products[entry['product']], pkg_path.relative_to(loadsdir)

        logger.info('Parsing {}...'.format(lp))
        yield lp.relative_to(loadsdir), [target_and_pkg(e) for e in lf]


class ValidationError(Exception):
    def __init__(self, check, context, msg):
        self.check = check
        self.context = context
        self.msg = msg

    def __str__(self):
        return 'Failed check {0.check} in {0.context}: {0.msg}'.format(self)


def validate(loadsdir, ticket=None, **kwargs):
    '''Perform various validity checks on the given 'loadsdir'.

    Each check is enabled/disabled by a corresponding boolean/flag keyword
    argument passed to this function. All checks are enabled/True by default.
    Here are the available checks/kwargs:

    loads_has_codec: All .loads files should refer to at least _one_ codec.pkg.
    loads_filename: All .loads files have a filename that reflects the contents
        within.
    loads_signed: All .loads files have a corresponding .loads.sgn file with a
        valid signature (SWIMS release signature if 'ticket' is given,
        otherwise test signature).
    product_exists: Product field in .loads file contains a real product name.
    pkg_relative: All .pkg references must be relative to loadsdir, no absolute
        filenames/URLs allowed.
    pkg_filename: All .pkg references have expected/preferred filenames.
    pkg_inside: All .pkg references from .loads files are within loadsdir.
    pkg_exists: All .pkg references from .loads files point to existing files.
    pkg_external_symlinks: Disallow .pkg symlinks pointing outside of loadsdir.
    pkg_attached: All .pkg files within loadsdir are referenced from at least
        _one_ .loads file. No "loose" .pkgs.
    pkg_version: Verify version field in .loads against the actual PKG.
    pkg_targets: Verify targets field in .loads against the actual PKG.
    pkg_checksum: Verify checksum field in .loads against the actual PKG.

    In order to verify release-signed .loads files, the 'loads_signed' check
    must be enable, AND 'ticket' must point to a valid SWIMS ticket file.

    Each failed check is _yielded_ (NOT raised) as a ValidationError instance.
    This allows a caller to iterate over the generated errors, and potentially
    abort the validation on the first (or any) error.
    '''

    checks = {
        'loads_has_codec': True,
        'loads_filename': True,
        'loads_signed': True,
        'product_exists': True,
        'pkg_relative': True,
        'pkg_filename': True,
        'pkg_inside': True,
        'pkg_exists': True,
        'pkg_external_symlinks': True,
        'pkg_attached': True,
        'pkg_version': True,
        'pkg_targets': True,
        'pkg_checksum': True,
    }
    checks.update(kwargs)

    # Reverse-map product names into targets
    Products = {t.product: t for t in loadsfile.Targets.values()}

    seen_pkgs = set()
    for loads_path, loads in walk(loadsdir):
        codecs, peripherals = [], []
        for entry in loads:
            pkg_ref = Path(entry['packageLocation'])

            if checks['pkg_relative']:
                if pkg_ref.is_absolute() or '://' in str(pkg_ref):
                    yield ValidationError('pkg_relative', loads_path,
                        '{} is absolute filename or URL'.format(pkg_ref))
            pkg_path = loads_path.parent / pkg_ref
            if checks['pkg_inside']:
                if loadsdir not in pkg_path.parents:
                    yield ValidationError('pkg_inside', loads_path,
                        '{} is not within {}'.format(pkg_path, loadsdir))
            if checks['pkg_exists']:
                if not pkg_path.is_file():
                    yield ValidationError('pkg_exists', loads_path,
                        '{} does not exist as a file'.format(pkg_path))
            if checks['pkg_external_symlinks']:
                if loadsdir.resolve() not in pkg_path.resolve().parents:
                    yield ValidationError('pkg_external_symlinks', loads_path,
                        '{} points outside {}'.format(pkg_path, loadsdir))
            seen_pkgs.add(pkg_path.resolve())

            try:
                target = Products[entry['product']]
            except KeyError:
                if checks['product_exists']:
                    yield ValidationError('product_exists', loads_path,
                        '{} is not a product name'.format(entry['product']))
                else:
                    continue

            if target.is_codec:
                codecs.append((target, pkg_path.name, entry['version']))
            else:
                peripherals.append((target, pkg_path.name))

            try:
                pkg = loadsfile.PkgFile(pkg_path)
                if checks['pkg_version']:
                    if entry['version'] != pkg.version:
                        yield ValidationError('pkg_version', pkg_path,
                            'Wrong PKG version ({} != {})'.format(
                                entry['version'], pkg.version))
                if checks['pkg_targets']:
                    if entry['targets'] != pkg.targets:
                        yield ValidationError('pkg_targets', pkg_path,
                            'Wrong PKG targets ({} != {})'.format(
                                entry['targets'], pkg.targets))
                if checks['pkg_checksum']:
                    if entry['checksum'] != pkg.checksum:
                        yield ValidationError('pkg_checksum', pkg_path,
                            'Wrong PKG checksum ({} != {})'.format(
                                entry['checksum'], pkg.checksum))
            except CalledProcessError:
                pass

        if checks['loads_has_codec'] and not codecs:
            yield ValidationError('loads_has_codec', loads_path,
                'No codec targets found in .loads file')
        if checks['loads_filename'] and codecs:
            if len(codecs) == 1:  # .loads file targets a single codec
                target, path, version = codecs[0]
                pref_name = preferred_pkg_filename(target, version, '.loads')
            else:  # .loads file targets multiple codecs
                raise NotImplementedError('What is the preferred filename for a super-loads?')
            if loads_path.name != pref_name:
                yield ValidationError('loads_filename', loads_path,
                    '{} is not the preferred filename ({})'.format(
                        loads_path.name, pref_name))
        if checks['pkg_filename'] and codecs:
            expect_version = codecs[0][2]  # all .pkgs should use same version
            for target, pkg_filename, *_ in codecs + peripherals:
                pref_name = preferred_pkg_filename(target, expect_version)
                if pkg_filename != pref_name:
                    yield ValidationError('pkg_filename', loads_path,
                        '{} is not the preferred filename ({})'.format(
                            pkg_filename, pref_name))

        if checks['loads_signed']:
            sgn_path = loads_path.with_suffix('.loads.sgn')
            if not sgn_path.is_file():
                yield ValidationError('loads_signed', loads_path,
                    '{} is missing'.format(sgn_path))
            if ticket is None:
                good = loadssign.test_verify(loads_path, sgn_path)
            else:
                good = loadssign.release_verify(loads_path, sgn_path, ticket)
            if not good:
                yield ValidationError('loads_signed', loads_path,
                    '{} is not a valid {} signature'.format(
                        sgn_path, 'release' if ticket else 'test'))

    if checks['pkg_attached']:
        for pkg in loadsdir.rglob('*.pkg'):
            if pkg.resolve() not in seen_pkgs:
                yield ValidationError('pkg_attached', pkg,
                    'Not referenced from any .loads file')


def main():
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description=sys.modules[__name__].__doc__)
    parser.add_argument(
        'destination', type=Path,
        help='Where to write the loads directory contents')
    parser.add_argument(
        '--target', '-t', action='append', choices=loadsfile.Targets,
        default=[], help='Targets to include in generated loads dir')
    parser.add_argument(
        '--file', '-f', action='append', type=Path, default=[],
        help='PKG path corresponding to each given --target option')
    parser.add_argument(
        '--version', default=None,
        help='Version number in .pkg/.loads filenames (default: from 1st PKG)')
    parser.add_argument(
        '--loads-name', default=None,
        help='File name used for .loads file (default: from 1st target)')
    parser.add_argument(
        '--symlink', action='store_true', default=True,
        help='Symlink PKG files into loads dir (this is the default behavior)')
    parser.add_argument(
        '--copy', dest='symlink', action='store_false',
        help='Copy PKG files into loads dir (instead of symlinking)')
    parser.add_argument(
        '--deps', '-d', action='store_true',
        help='Automatically include dependencies of the given target')
    parser.add_argument(
        '--objdir', '-O', default=None,
        help='Look here for dependents\' build output (default: _build)')
    parser.add_argument(
        '--validate', action='store_true',
        help='Validate any .loads and .pkg files found within the loads dir.')
    parser.add_argument(
        '--ticket', type=Path, default=None,
        help='Use this SWIMS ticket to verify .loads signatures.')
    parser.add_argument(
        '--serve', action='store_true',
        help='Serve loads dir over HTTP until you press Ctrl+C.')

    args = parser.parse_args()

    args.target = [loadsfile.Targets[name] for name in args.target]

    if args.deps:
        assert len(args.target) == 1
        assert len(args.file) <= 1

        args.target, args.file = zip(*verify_pkgs(find_target_deps_and_pkgs(
            args.target[0], args.file[0] if args.file else None, args.objdir)))

    if args.target and len(args.target) != len(args.file):
        parser.error(
            'Must specify pairs of corresponding --target and --file options!')

    if args.target:
        print(build(
            args.destination,
            targets=args.target,
            pkgs=args.file,
            version=args.version,
            loads_fname=args.loads_name,
            symlink=args.symlink))

    if args.validate:
        errors = 0
        for error in validate(
            args.destination,
            ticket=args.ticket,
            pkg_external_symlinks=not args.symlink,
        ):
            logger.error(error)
            errors += 1
        if errors:
            print('{} validation errors found!'.format(errors))
            return errors

    if args.serve:
        loads_paths = sorted(p for p, f in walk(args.destination))
        assert loads_paths
        httpd = http_server(args.destination)
        print()
        print('Serving these loads files:')
        print()
        prefix = 'http://{}:{}/'.format(
            loadsutil.guess_my_ip(), httpd.server_address[1])
        for path in loads_paths:
            print(prefix / path.relative_to(args.destination))
        print()
        print('Press Ctrl+C to stop the server at any time.')
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            httpd.server_close()
            print('Stopped by user!')
        print('Loads directory remains at {}'.format(args.destination))

    return 0

if __name__ == '__main__':
    sys.exit(main())
