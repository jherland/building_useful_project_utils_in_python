#!/usr/bin/env python3
'''Utility for building .loads files around our build products.

.loads file are JSON documents that declare a collection of related software
images (.pkg files) for one or more of our products. .loads files are an
important part of how we distribute software upgrades for our products.

See https://rdwiki.cisco.com/wiki/Swupgrade for more details.
'''

from functools import lru_cache
import json
import logging
from pathlib import Path
import subprocess
import sys

from loadsutil import sha512sum


logger = logging.getLogger('loadsfile')

PKGEXTRACT = 'pkgextract'  # Assume this is in $PATH


class Target:
    '''Encapsulate a build target and its metadata.'''
    def __init__(self, name, product, is_codec, deps, cucm_ids):
        self.name = name
        self.product = product
        self.is_codec = is_codec
        self.deps = deps
        self.cucm_ids = cucm_ids

    def __str__(self):
        return self.name

    def __repr__(self):
        return '{}({!r})'.format(self.__class__.__name__, self.name)

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, other):
        return self.name == other.name


# Map build target names to externally-visible product names and other metadata
Targets = {t[0]: Target(*t) for t in [
    # Codecs:
    # The product name MUST match the codec_type argument passed to the
    # SWUpgradehandler constructor.

    # Older codecs bundle everything inside the codec.pkg, hence have no deps:
    ('asterix', 's52010', True, [], [626, 689, 690]),
    ('asterix.nocrypto', 's52011', True, [], []),  # TODO: CUCM IDs???
    ('carbon', 's52020', True, [], [688, 36207, 36208, 36227]),
    ('drishti', 's52030', True, [], [682]),
    ('tempo', 's52040', True, [], [36239, 36241]),

    # Newer codecs need their peripherals referenced in their .loads file:
    ('sunrise', 's53200', True, ['halley', 'moody', 'pyramid'], [
        36251, 36254, 36255, 36259, 36265]),
    ('zenith', 's53300', True, ['halley', 'moody', 'pyramid', 'idefix'], [
        36753]),

    # Peripherals:
    # The product name MUST match the string returned from that peripheral's
    # implementation of SoftwareUpgrade::Peripheral::getPeripheralId()
    ('halley', 'Precision 60 Camera', False, [], []),
    ('moody', 'SpeakerTrack 60', False, [], []),
    ('pyramid', 'Pyramid', False, [], []),
    ('idefix', 'Idefix', False, [], []),
]}


class PkgFile:
    '''Cache some details about the PKG file at the given path.'''

    def __init__(self, path):
        self.path = path
        assert path.is_file()
        self._targets = None
        self._version = None
        self._checksum = None

    @property
    def targets(self):
        if self._targets is None:
            self._targets = subprocess.check_output(
                [PKGEXTRACT, '-T', '-f', str(self.path)],
                stderr=subprocess.STDOUT,
                universal_newlines=True,
            ).rstrip().split(',')
        return self._targets

    @property
    def version(self):
        if self._version is None:
            self._version = subprocess.check_output(
                [PKGEXTRACT, '-u', '-f', str(self.path)],
                stderr=subprocess.STDOUT,
                universal_newlines=True,
            ).rstrip()
        return self._version

    @property
    def checksum(self):
        if self._checksum is None:
            self._checksum = sha512sum(self.path)
        return self._checksum


class PkgLoads:
    '''A .pkg.loads file already contains a JSON fragment for the .pkg.'''

    def __init__(self, pkg_path):
        self.pkg_path = pkg_path
        assert pkg_path.is_file()
        self.loads_path = Path(str(pkg_path) + '.loads')
        if not self.loads_path.is_file():
            raise ValueError('{} not found'.format(self.loads_path))
        if self.loads_path.stat().st_mtime < self.pkg_path.stat().st_mtime:
            raise ValueError('{} out of date'.format(self.loads_path))
        with self.loads_path.open() as f:
            fragment = json.load(f)
        keys = {'product', 'packageLocation', 'version', 'targets', 'checksum'}
        if len(fragment) != 1 or not keys.issubset(fragment[0].keys()):
            raise ValueError('{} is malformed')
        # Everything seems good, adopt values
        for k in keys:
            setattr(self, k, fragment[0][k])


@lru_cache(maxsize=None)
def pkg_info(target, pkg_path):
    '''Return PkgLoads or PkgFile instance for the given pkg_path.'''
    try:  # use an up-to-date pre-generated .pkg.loads file, if available
        pkg = PkgLoads(pkg_path)
        if target.product != pkg.product:
            raise ValueError(
                'Wrong product: {} != {}'.format(target.product, pkg.product))
    except Exception as e:  # fall back to pkgextract + sha512sum(pkg)
        logger.warning('{}, fall back to slow path...'.format(e))
        pkg = PkgFile(pkg_path)
    return pkg


class LoadsFile:
    @classmethod
    def parse(cls, path):
        def non_empty_str(s):
            return isinstance(s, str) and len(s) > 0

        def list_of_strings(l):
            return isinstance(l, list) and all(isinstance(s, str) for s in l)

        validate_keys = {
            'product': non_empty_str,
            'packageLocation': non_empty_str,
            'version': non_empty_str,
            'targets': list_of_strings,
            'checksum': non_empty_str,
        }

        with path.open() as f:
            loads = json.load(f)
        assert isinstance(loads, list)
        for entry in loads:
            assert isinstance(entry, dict)
            assert set(entry.keys()) == set(validate_keys.keys())
            assert all(validate_keys[k](v) for k, v in entry.items())
        return cls(loads=loads)

    def __init__(self, verify=False, loads=None):
        self.verify = verify
        self.loads = [] if loads is None else loads

    def __iter__(self):
        return iter(self.loads)

    def add(self, target, pkg_path, url):
        pkg = PkgFile(pkg_path) if self.verify else pkg_info(target, pkg_path)
        self.loads.append({
            'product': target.product,
            'packageLocation': url,
            'version': pkg.version,
            'targets': pkg.targets,
            'checksum': pkg.checksum,
        })

    def write(self, f):
        json.dump(self.loads, f, indent=4)


def main():
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description=sys.modules[__name__].__doc__)
    parser.add_argument(
        '--target', '-t', action='append', choices=Targets, default=[],
        help='Target to include in generated .loads file')
    parser.add_argument(
        '--file', '-f', action='append', type=Path, default=[],
        help='PKG path corresponding to each given --target option')
    parser.add_argument(
        '--output', '-o', type=argparse.FileType('w'), default='-',
        help='Where to write the resulting .loads file (default: stdout)')
    parser.add_argument(
        '--base-url', default='',
        help='Prefix to PKG filenames in resulting .loads file (default: "")')
    parser.add_argument(
        '--pkgextract',
        help='Path to pkgextract binary (default: find in $PATH)')
    parser.add_argument(
        '--verify', action='store_true',
        help='Do not reuse existing .pkg.loads files.')

    args = parser.parse_args()
    if len(args.target) != len(args.file):
        parser.error(
            'Must specify pairs of corresponding --target and --file options!')
    if args.pkgextract:
        global PKGEXTRACT
        PKGEXTRACT = args.pkgextract

    # Disable .pkg.loads optimization when we're writing to a .pkg.loads file.
    if args.output.name.endswith('.pkg.loads'):
        args.verify = True

    if args.base_url and not args.base_url.endswith('/'):
        args.base_url += '/'

    loads = LoadsFile(args.verify)
    for target, pkg_path in zip(args.target, args.file):
        loads.add(Targets[target], pkg_path, args.base_url + pkg_path.name)
    loads.write(args.output)


if __name__ == '__main__':
    main()
