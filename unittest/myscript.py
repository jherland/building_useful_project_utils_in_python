#!/usr/bin/env python3
'''Calculate the square of a given number.'''

import argparse
import sys
import unittest


def calc(num, exp):
    return num ** exp


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
    main()


class TestSquare(unittest.TestCase):

    def test_zero_squared_is_zero(self):
        self.assertEqual(0, calc(0, 2))

    def test_one_squared_is_one(self):
        self.assertEqual(1, calc(1, 2))

    def test_two_squared_is_four(self):
        self.assertEqual(4, calc(2, 2))


class TestCube(unittest.TestCase):

    def test_zero_cubed_is_zero(self):
        self.assertEqual(0, calc(0, 3))

    def test_one_cubed_is_one(self):
        self.assertEqual(1, calc(1, 3))

    def test_two_cubed_is_eight(self):
        self.assertEqual(8, calc(2, 3))
