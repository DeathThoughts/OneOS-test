#!/usr/bin/env python3
"""Distribute the existing media-share unittest suite without dropping any tests."""
import argparse
import importlib.util
from pathlib import Path
import sys
import unittest


def tests():
    path = Path(__file__).with_name("test-guest-media-share.py")
    spec = importlib.util.spec_from_file_location("oneos_media_tests", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)

    def flatten(node):
        if isinstance(node, unittest.TestSuite):
            for item in node:
                yield from flatten(item)
        else:
            yield node

    cases = sorted(flatten(suite), key=lambda case: case.id())
    if not cases or len(cases) != len({case.id() for case in cases}):
        raise RuntimeError("Empty or duplicate media test IDs")
    # Sharding should not silently change class/module fixture semantics.
    for case in cases:
        cls = type(case)
        for name in ("setUpClass", "tearDownClass"):
            if getattr(cls, name).__func__ is not getattr(unittest.TestCase, name).__func__:
                raise RuntimeError("Media tests now have a shared class fixture; review sharding")
    if any(hasattr(module, name) for name in ("setUpModule", "tearDownModule")):
        raise RuntimeError("Media tests now have a module fixture; review sharding")
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--index", type=int)
    args = parser.parse_args()
    cases = tests()
    if not 1 <= args.count <= len(cases):
        parser.error("invalid shard count")
    shards = [cases[i::args.count] for i in range(args.count)]
    ids = [case.id() for shard in shards for case in shard]
    if len(ids) != len(cases) or set(ids) != {case.id() for case in cases}:
        raise RuntimeError("Media shard coverage mismatch")
    if args.check:
        print(f"{len(cases)} tests, {args.count} shards: {[len(s) for s in shards]}")
        return 0
    if not 0 <= args.index < args.count:
        parser.error("invalid shard index")
    chosen = shards[args.index]
    print(f"Media safety shard {args.index + 1}/{args.count}: {len(chosen)} of {len(cases)} tests", flush=True)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(chosen))
    return 0 if result.wasSuccessful() and result.testsRun == len(chosen) else 1


if __name__ == "__main__":
    sys.exit(main())
