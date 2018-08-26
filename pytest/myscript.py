#!/usr/bin/env python3
'''Calculate the square of a given number.'''

import argparse
import sys


def calc(num, exp):
    return num ** exp


def test_calc():
    vectors = [
        (0, 2, 0), (1, 2, 1), (2, 2, 4),
        (0, 3, 0), (1, 3, 1), (2, 3, 8),
    ]
    for num, exp, expect in vectors:
        assert calc(num, exp) == expect


def main():
    parser = argparse.ArgumentParser(description=sys.modules[__name__].__doc__)
    parser.add_argument(
        'num', type=int,
        help='the number to be squared')
    parser.add_argument(
        '--cube', '-3', action='store_true',
        help='calculate the cube (instead of square)')
    parser.add_argument(
        '--file', '-f', type=argparse.FileType('w'), default=sys.stdout,
        help='write result here (stdout by default)')

    args = parser.parse_args()
    result = calc(args.num, 3 if args.cube else 2)
    print(result, file=args.file)


if __name__ == '__main__':
#    import pytest
#    pytest.main([__file__])
    main()
