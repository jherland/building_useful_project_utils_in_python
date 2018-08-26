import hashlib
from pathlib import Path
import subprocess
import socket
import sys


# Assume this file is located one level below main repo root ($MAIN/bin/util.py)
MAIN_ROOT = Path(sys.modules[__name__].__file__).resolve().parent.parent


def sha512sum(path):
    '''Return the SHA512 checksum of the file contents at the given path.'''
    READ_SIZE = 1024 * 1024  # 1MB blocks
    d = hashlib.sha512()
    with path.open('rb') as f:
        while True:
            chunk = f.read(READ_SIZE)
            if not chunk:
                break
            d.update(chunk)
    return d.hexdigest()


def ip_route(addr):
    '''Consult the local routing tables for how to contact the given 'addr'.

    Returns a dictionary with keys 'via', 'dev', 'src', and corresponding
    values from the output of "ip -o route get $addr".
    '''
    argv = ['ip', '-o', 'route', 'get', addr]
    line = subprocess.check_output(argv, universal_newlines=True).rstrip()
    words = line.split(' ')
    nextword = {w: nw for w, nw in zip(words, words[1:])}
    return {
        'via': nextword.get('via', None),
        'dev': nextword['dev'],
        'src': nextword['src'],
    }


def guess_my_ip(from_addr=None):
    '''Guess which IP address can be used to reach this machine.

    This does NOT take into account routers, firewalls, NAT or anything else
    that may complicate how a remote machine would reach us. Instead, it merely
    returns the source IP address we would use when contacting 'from_addr'.
    '''
    if from_addr is None:
        from_addr = '8.8.8.8'
    else:
        addrs = socket.getaddrinfo(from_addr, 80, proto=socket.IPPROTO_TCP)
        from_addr = addrs[0][-1][0]
    return ip_route(from_addr)['src']
