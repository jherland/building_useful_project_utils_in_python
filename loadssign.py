#!/usr/bin/env python3
'''Utility for signing .loads files with local test keys or SWIMS release keys.

.loads files must be signed in order for their contents to be trustworthy. This
is an important part of how we distribute software upgrades for our products.

See https://rdwiki.cisco.com/wiki/Swupgrade for more details.
'''

import base64
import getpass
import json
import logging
from pathlib import Path
import subprocess
import sys

import loadsutil


logger = logging.getLogger('loadssign')

TEST_SIGNING_KEY = loadsutil.MAIN_ROOT / 'build/pki/rsa_test_signing_key.pem'
TEST_SIGNING_CERT = loadsutil.MAIN_ROOT / 'build/pki/rsa_test_signing_cert.pem'
SWIMS_CLIENT = loadsutil.MAIN_ROOT / 'build/abraxas/swims_client.py'
SWIMS_PRODUCT = 'ccmPatch_cop3'
SWIMS_KEY_ARGS = [
    '-keyType=DEV',
    '-keyName=' + SWIMS_PRODUCT,
    '-product=' + SWIMS_PRODUCT,
]
DEFAULT_TICKET_PATH = Path.cwd() / '.swims_ticket'


def create_swims_ticket(cec_user, otp_code, ticket=DEFAULT_TICKET_PATH, *,
                        valid_hours=4, max_uses=10, reason='testing'):
    logger.info('Creating SWIMS ticket in {}'.format(ticket))
    argv = [
        SWIMS_CLIENT, 'ticket', 'create',
        '-products', 'ccmPatch_cop3',
        '-ticketType', 'DEV',
        '-validityHours', str(valid_hours),
        '-maxUses', str(max_uses),
        '-reason', reason,
        '-authType', 'OTP',
        '-username1', cec_user,
        '-password1', otp_code,
        '-out', str(ticket),
    ]
    logger.debug(argv)
    subprocess.check_call(argv)


def test_sign(path, store=None, *, key=None):
    '''Test-sign the file at 'path'.

    The generated signature is returned as raw bytes. If 'store' is given, the
    signature is written to that path instead, and None is returned.

    If 'key' is not given, it defaults to build/pki/rsa_test_signing_key.pem in
    this main tree. If you want to sign with a SWIMS release key, you CANNOT
    use this function.
    '''
    if key is None:
        key = TEST_SIGNING_KEY
    assert path.is_file() and key.is_file()
    logger.info('Test signing {} with key {}'.format(path, key))
    argv = ['openssl', 'dgst', '-sign', str(key), '-sha512']
    if store:
        argv += ['-out', str(store), str(path)]
        logger.debug(argv)
        subprocess.check_call(argv)
    else:
        argv += [str(path)]
        logger.debug(argv)
        return subprocess.check_output(argv, universal_newlines=False)


def release_sign(path, ticket=DEFAULT_TICKET_PATH, store=None, notes='nothing'):
    '''Release-sign the file at 'path' using the given 'ticket'.

    The generated signature is returned as raw bytes. If 'store' is given, the
    signature is written to that path instead, and None is returned.

    The given 'ticket' must point to a valid SWIMS ticket.
    '''
    assert path.is_file() and ticket.is_file()
    logger.info('Release signing {} with SWIMS ticket {}'.format(path, ticket))
    argv = [SWIMS_CLIENT, 'abraxas', 'signHash'] + SWIMS_KEY_ARGS
    argv += [
        '-pid=UCL-UCM-LIC-K9',
        '-notes=' + notes,
        '-authType=Ticket',
        '-ticket=' + str(ticket),
        '-algorithm=SHA512',
        '-hash=' + loadsutil.sha512sum(path),
    ]

    logger.debug(argv)
    sgn_base64 = json.loads(subprocess.check_output(argv))['signature']
    sgn = base64.b64decode(sgn_base64)
    if store is None:
        return sgn

    with store.open('wb') as f:
        f.write(sgn)


def pubkey_from_cert(cert=None):
    '''Extract and return the public key from the given PEM certificate.

    If no cert is specifies, default to test S/W certificate found at
    build/pki/rsa_test_signing_cert.pem.
    '''
    if cert is None:
        cert = TEST_SIGNING_CERT
    argv = ['openssl', 'x509', '-in', str(cert), '-pubkey']
    logger.debug(argv)
    return subprocess.check_output(argv)


def pubkey_from_swims_ticket(ticket=DEFAULT_TICKET_PATH):
    '''Extract and return the public key from the given SWIMS ticket.

    Executes the 'fetchPublicKey' command against the SWIMS service, and
    returns the public key string in PEM format.
    '''
    assert ticket.is_file()
    argv = [SWIMS_CLIENT, 'abraxas', 'fetchPublicKey'] + SWIMS_KEY_ARGS
    argv.append('-ticket=' + str(ticket))
    before = b'-----BEGIN PUBLIC KEY-----\n'
    logger.info('Fetching public key from SWIMS with ticket {}'.format(ticket))
    logger.debug(argv)
    pkey = json.loads(subprocess.check_output(argv))['publicKey'].encode('utf8')
    after = b'\n-----END PUBLIC KEY-----\n'
    return before + pkey + after


def verify(path, sgn_path, pubkey):
    '''Verify that 'sgn_path' contains a valid signature for 'path'.

    Use the given 'pubkey' to verify. Return True on successful verification,
    otherwise False.
    '''
    assert isinstance(pubkey, bytes)
    logger.info('Verifying signature in {} against {}'.format(sgn_path, path))

    argv = ['openssl', 'dgst', '-sha512', '-verify', '-', '-signature',
            str(sgn_path), str(path)]
    try:
        result = subprocess.check_output(argv, input=pubkey).rstrip()
        return result == b'Verified OK'
    except subprocess.CalledProcessError:
        pass
    return False


def test_verify(path, sgn_path, *, cert=None):
    return verify(path, sgn_path, pubkey_from_cert(cert))


def release_verify(path, sgn_path, ticket=DEFAULT_TICKET_PATH):
    return verify(path, sgn_path, pubkey_from_swims_ticket(ticket))


def main():
    import argparse

    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description=sys.modules[__name__].__doc__)

    parser.add_argument(
        '--release', action='store_true',
        help='Sign/verify with key/certificate for release S/W')
    parser.add_argument(
        '--test', dest='release', action='store_false',
        help='Sign/verify with key/certificate for test S/W')

    subcommands = parser.add_subparsers(dest='subcommand')

    cmd_ticket = subcommands.add_parser(
        'ticket', help='Create a SWIMS ticket for signing release S/W')
    cmd_ticket.add_argument(
        'cec_user', nargs='?', default=getpass.getuser(),
        help='CEC username (default: current username)')
    cmd_ticket.add_argument(
        'otp_code',
        help='Code from OTP device (aka. HardToken)')
    cmd_ticket.add_argument(
        '--ticket', type=Path, default=DEFAULT_TICKET_PATH,
        help='Store the ticket here')
    cmd_ticket.add_argument(
        '--valid-hours', type=int, default=4,
        help='Ticket valid for how many hours (default: 4 hours)')
    cmd_ticket.add_argument(
        '--max-uses', type=int, default=10,
        help='Ticket valid for how many uses (default: 10 uses)')
    cmd_ticket.add_argument(
        '--reason', default='testing',
        help='Reason for creating ticket (default: "testing")')

    cmd_sign = subcommands.add_parser(
        'sign', help='Generate a .loads signature')
    cmd_sign.add_argument(
        'plaintext', type=Path,
        help='File (.loads) to sign')
    cmd_sign.add_argument(
        'signature', type=Path, nargs='?', default=None,
        help='Where to store the output (default: [plaintext.loads].sgn)')
    g = cmd_sign.add_mutually_exclusive_group()
    g.add_argument(
        '--key', type=Path, default=None,
        help='Sign with this private key (default: test S/W key from Main)')
    g.add_argument(
        '--ticket', type=Path, default=DEFAULT_TICKET_PATH,
        help='Sign with SWIMS service using this SWIMS ticket (release only)')

    cmd_verify = subcommands.add_parser(
        'verify', help='Verify a .loads signature')
    cmd_verify.add_argument(
        'plaintext', type=Path,
        help='File (.loads) for which to verify signature')
    cmd_verify.add_argument(
        'signature', type=Path, nargs='?', default=None,
        help='Signature to be verified (default: [plaintext.loads].sgn)')
    g = cmd_verify.add_mutually_exclusive_group()
    g.add_argument(
        '--cert', type=Path, default=None,
        help='Extract pubkey from certificate (default: test S/W cert from Main)')
    g.add_argument(
        '--ticket', type=Path, default=DEFAULT_TICKET_PATH,
        help='Fetch pubkey from SWIMS service with this SWIMS ticket')
    g.add_argument(
        '--pubkey', type=Path, default=None,
        help='Verify test/release S/W with this public key')

    args = parser.parse_args()

    if args.subcommand is None:
        parser.print_help()
        return

    if args.subcommand == 'ticket':
        create_swims_ticket(
            args.cec_user,
            args.otp_code,
            args.ticket,
            valid_hours=args.valid_hours,
            max_uses=args.max_uses,
            reason=args.reason
        )
        print(
            'SWIMS ticket ({0.valid_hours}h, {0.max_uses} uses, "{0.reason}") '
            'for {0.cec_user} stored in {0.ticket}'.format(args))
        return

    if args.plaintext.suffix != '.loads':
        parser.error('Must specify .loads file on command line!')

    if args.signature is None:  # -> [plaintext.loads].sgn
        args.signature = args.plaintext.with_suffix('.loads.sgn')

    if args.subcommand == 'sign':
        if args.release:
            release_sign(args.plaintext, args.ticket, store=args.signature)
        else:
            test_sign(args.plaintext, store=args.signature, key=args.key)

        print('{} signature for {} stored in {}'.format(
            'Release' if args.release else 'Test',
            args.plaintext,
            args.signature))
    elif args.subcommand == 'verify':
        if args.pubkey:  # Valid in both --release and --test mode
            pubkey = args.pubkey.open('rb').read()
        elif args.release:
            pubkey = pubkey_from_swims_ticket(args.ticket)
        else:
            pubkey = pubkey_from_cert(args.cert)

        verified = verify(args.plaintext, args.signature, pubkey)
        print('{} is {} a valid {} signature for {}'.format(
            args.signature,
            'indeed' if verified else 'NOT',
            'release' if args.release else 'test',
            args.plaintext))
        sys.exit(0 if verified else 1)
    else:
        parser.error('Unknown subcommand {}!'.format(args.subcommand))


if __name__ == '__main__':
    main()
