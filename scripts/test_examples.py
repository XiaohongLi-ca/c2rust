#!/usr/bin/env python3

import argparse
import errno
import logging
import os
import multiprocessing
import re
import sys

from common import (
    config as c,
    pb,
    Colors,
    die,
    get_cmd_or_die,
    invoke,
    regex,
    setup_logging,
    transpile
)

cargo = get_cmd_or_die('cargo')
git = get_cmd_or_die('git')
intercept_build = get_cmd_or_die('intercept_build')
make = get_cmd_or_die('make')
python = get_cmd_or_die('python')
rustc = get_cmd_or_die('rustc')

NUM_JOBS = multiprocessing.cpu_count()

EXAMPLES = [
    'genann',
    'grabc',
    'libxml2',
    'lil',
    'snudown',
    'tmux',
    'urlparser',
    'xzoom'
]


def build_path(path, new: str, is_dir: bool) -> str:
    err_msg = "`{}` does not exist in {}".format(new, path)

    new_path = os.path.join(path, new)
    if is_dir:
        assert os.path.isdir(new_path), err_msg
    else:
        assert os.path.isfile(new_path), err_msg
    return new_path


def print_blue(msg: str):
    print(Colors.OKBLUE + msg + Colors.NO_COLOR)


class Test:
    def __init__(self, args=None):
        self.args = args
        self.project_name = ''
        self.transpiler_args = []
        self.ib_cmd = []
        self.example_dir = ''
        self.repo_dir = ''
        # Source directory where `Crate` files will live,
        # e.g. `c2rust-build` or `rust`(tmux and libxml2)
        self.rust_src = ''
        self.cc_db = ''

    def init_submodule(self):
        if self.args.regex_examples.fullmatch(self.project_name):
            print_blue("Initializing {}...".format(self.project_name))
            with pb.local.cwd(self.example_dir):
                invoke(git, ['submodule', 'update', '--init', 'repo'])

    def deinit_submodule(self):
        if self.args.regex_examples.fullmatch(self.project_name) and self.args.deinit:
            print_blue("Deinitializing {}...".format(self.project_name))
            with pb.local.cwd(self.example_dir):
                invoke(git, ['submodule', 'deinit', 'repo', '-f'])

    # Should be used on projects that utilize GNU Build Systems
    def autotools(self):
        with pb.local.cwd(self.repo_dir):
            invoke(pb.local['./autogen.sh'])
            with pb.local.env(CFLAGS="-g -O0"):
                invoke(pb.local['./configure'])

    # `gen_cc_db` generates the `compile_commands.json` for a project
    def gen_cc_db(self):
        with pb.local.cwd(self.repo_dir):
            invoke(make, ['clean'])
            invoke(intercept_build, *self.ib_cmd)
            self.cc_db = build_path(self.repo_dir, 'compile_commands.json',
                                    is_dir=False)

    # `transpile` in most cases runs the transpile function from `common.py`,
    # which in turn just calls `c2rust transpile *args`
    def transpile(self):
        with pb.local.cwd(self.repo_dir):
            transpile(self.cc_db,
                      emit_build_files=False,
                      extra_transpiler_args=self.transpiler_args)

    # `build` is the main builder function, this is where either the `Crate`
    # will be built or rustc will be called directly
    def build(self):
        with pb.local.cwd(self.rust_src):
            invoke(cargo, ['build', '-j{}'.format(NUM_JOBS)])

    def test(self):
        pass

    def build_and_test(self):
        self.gen_cc_db()
        self.transpile()
        self.build()
        self.test()


class Genann(Test):
    def __init__(self, args):
        self.args = args
        self.project_name = 'genann'
        self.transpiler_args = ['--emit-build-files', '--overwrite-existing']
        self.ib_cmd = ['make']
        self.example_dir = build_path(
            c.EXAMPLES_DIR, self.project_name, is_dir=True)
        self.repo_dir = build_path(self.example_dir, 'repo', is_dir=True)
        self.rust_src = os.path.join(self.repo_dir, 'c2rust-build')
        self.init_submodule()

    def __del__(self):
        self.deinit_submodule()

    def test(self):
        rm = get_cmd_or_die('rm')
        ln = get_cmd_or_die('ln')
        for N in (1, 4):
            test = 'example{}'.format(N)
            with pb.local.cwd(self.repo_dir):
                invoke(rm, ['-rf', self.rust_src])

            self._transpile_example(test)
            with pb.local.cwd(self.rust_src):
                # Create a link to the example data files
                invoke(ln, ['-sf', build_path(self.repo_dir, 'example', True)])
                invoke(cargo, ['run'])

    # Helper function that transpiles whatever test is
    # passed in as `main`
    def _transpile_example(self, main: str):
        transpile(self.cc_db,
                  emit_build_files=False,
                  extra_transpiler_args=['--emit-build-files', '--main', main])
        self.rust_src = build_path(self.repo_dir, 'c2rust-build',
                                   is_dir=True)


class Grabc(Test):
    def __init__(self, args):
        self.args = args
        self.project_name = 'grabc'
        self.transpiler_args = ['--overwrite-existing']
        self.ib_cmd = ['make']
        self.example_dir = build_path(
            c.EXAMPLES_DIR, self.project_name, is_dir=True)
        self.repo_dir = build_path(self.example_dir, 'repo', is_dir=True)
        self.build_flags = ['grabc.rs', '-L/usr/x11R6/lib', '-lX11']
        self.init_submodule()

    def __del__(self):
        self.deinit_submodule()

    def build(self):
        with pb.local.cwd(self.repo_dir):
            invoke(rustc, *self.build_flags)


class Libxml2(Test):
    def __init__(self, args):
        self.args = args
        self.project_name = 'libxml2'
        self.transpiler_args = []
        self.ib_cmd = ['make', 'check', '-j{}'.format(NUM_JOBS)]
        self.example_dir = build_path(
            c.EXAMPLES_DIR, self.project_name, is_dir=True)
        self.repo_dir = build_path(self.example_dir, 'repo', is_dir=True)
        self.build_flags = []
        self.init_submodule()
        self.rust_src = os.path.join(self.repo_dir, 'rust')

    def __del__(self):
        self.deinit_submodule()

    def gen_cc_db(self):
        self.autotools()
        with pb.local.cwd(self.repo_dir):
            invoke(make, ['clean'])
            invoke(intercept_build, *self.ib_cmd)

    def transpile(self):
        with pb.local.cwd(self.example_dir):
            invoke(pb.local['./translate.py'])
            invoke(pb.local['./patch_translated_code.py'])

    # Iterates through the list of tests, and then runs each one
    def test(self):
        # testname -> input_file
        tests = {
            "xmllint": ['test/bigname.xml'],
            "runtest": [],
            "testapi": [],
            "testSAX": [],
            "testURI": ['test/bigname.xml'],
            "testdict": [],
            "testHTML": ['test/HTML/html5_enc.html'],
            "testC14N": ['--', '--with-comments', 'test/c14n/with-comments/example-7.xml'],
            "testchar": [],
            "testRelax": ['test/bigname.xml'],
            "testXPath": ['test/bigname.xml'],
            "testModule": [],
            "testlimits": [],
            # "testReader", Not working at the moment
            "testRegexp": ['test/regexp'],
            "testrecurse": [],
            "testSchemas": ['test/schemas/all_0.xsd'],
            "testThreads": [],
            "testAutomata": ['test/automata/po'],
        }

        for test, input_file in tests.items():
            with pb.local.cwd(self.rust_src):
                example_args = ['run', '--example', test]
                example_args.extend(input_file)
                invoke(cargo, *example_args)


class Lil(Test):
    def __init__(self, args):
        self.args = args
        self.project_name = 'lil'
        self.transpiler_args = ['--emit-build-files', '-m', 'main',
                                '--overwrite-existing']
        self.ib_cmd = ['make']
        self.example_dir = build_path(
            c.EXAMPLES_DIR, self.project_name, is_dir=True)
        self.repo_dir = build_path(self.example_dir, 'repo', is_dir=True)
        self.build_flags = []
        self.rust_src = os.path.join(self.repo_dir, 'c2rust-build')
        self.init_submodule()

    def __del__(self):
        self.deinit_submodule()


class Snudown(Test):
    def __init__(self, args):
        self.args = args
        self.project_name = 'snudown'
        self.transpiler_args = ['--overwrite-existing']
        self.ib_cmd = []
        self.example_dir = build_path(
            c.EXAMPLES_DIR, self.project_name, is_dir=True)
        self.repo_dir = build_path(self.example_dir, 'repo', is_dir=True)
        self.build_flags = ['setup.py', 'build', '--translate']
        self.init_submodule()

    def __del__(self):
        self.deinit_submodule()

    def gen_cc_db(self):
        pass

    def transpile(self):
        pass

    def build(self):
        with pb.local.cwd(self.repo_dir):
            invoke(python, *self.build_flags)

    def test(self):
        with pb.local.cwd(self.repo_dir):
            invoke(python, ['setup.py', 'test'])

class TinyCC(Test):
    def __init__(self, args):
        self.args = args
        self.project_name = 'tinycc'
        self.transpiler_args = []
        self.ib_cmd = ['make', '-j{}'.format(NUM_JOBS)]
        self.example_dir = build_path(
            c.EXAMPLES_DIR, self.project_name, is_dir=True)
        self.repo_dir = build_path(self.example_dir, 'repo', is_dir=True)
        self.build_flags = []
        self.init_submodule()
        self.rust_src = os.path.join(self.repo_dir, 'rust')

    def __del__(self):
        self.deinit_submodule()

    def autotools(self):
        os.chdir(self.repo_dir)
        invoke(pb.local['./configure'])

    def gen_cc_db(self):
        self.autotools()
        with pb.local.cwd(self.repo_dir):
            invoke(make, ['clean'])
            invoke(intercept_build, *self.ib_cmd)

    def transpile(self):
        with pb.local.cwd(self.example_dir):
            invoke(pb.local['./translate.py'])

    def test(self):
        with pb.local.cwd(self.repo_dir):
            invoke(make, ['rust-test'])

class Tmux(Test):
    def __init__(self, args):
        self.args = args
        self.project_name = 'tmux'
        self.transpiler_args = []
        self.ib_cmd = ['make', 'check', '-j{}'.format(NUM_JOBS)]
        self.example_dir = build_path(
            c.EXAMPLES_DIR, self.project_name, is_dir=True)
        self.repo_dir = build_path(self.example_dir, 'repo', is_dir=True)
        self.build_flags = []
        self.init_submodule()
        self.rust_src = os.path.join(self.repo_dir, 'rust')

    def __del__(self):
        self.deinit_submodule()

    def gen_cc_db(self):
        self.autotools()
        with pb.local.cwd(self.repo_dir):
            invoke(make, ['clean'])
            invoke(intercept_build, *self.ib_cmd)

    def transpile(self):
        with pb.local.cwd(self.example_dir):
            invoke(pb.local['./translate.py'])


class Urlparser(Test):
    def __init__(self, args):
        self.args = args
        self.project_name = 'urlparser'
        self.transpiler_args = ['--overwrite-existing']
        self.ib_cmd = ['make']
        self.example_dir = build_path(
            c.EXAMPLES_DIR, self.project_name, is_dir=True)
        self.repo_dir = build_path(self.example_dir, 'repo', is_dir=True)
        self.build_flags = ['test.rs']
        self.init_submodule()

    def __del__(self):
        self.deinit_submodule()

    def build(self):
        with pb.local.cwd(self.repo_dir):
            invoke(rustc, *self.build_flags)


class Xzoom(Test):
    def __init__(self, args):
        self.args = args
        self.project_name = 'xzoom'
        self.transpiler_args = ['--overwrite-existing']
        self.ib_cmd = ['sh', '-c',
                       'clang xzoom.c -L/usr/X11R6/lib -lX11 -DTIMER']
        self.example_dir = build_path(
            c.EXAMPLES_DIR, self.project_name, is_dir=True)
        self.repo_dir = build_path(self.example_dir, 'repo', is_dir=True)
        self.build_flags = ['xzoom.rs', '-L/usr/x11R6/lib', '-lX11']
        self.init_submodule()

    def __del__(self):
        self.deinit_submodule()

    def gen_cc_db(self):
        with pb.local.cwd(self.repo_dir):
            invoke(intercept_build, *self.ib_cmd)
            self.cc_db = build_path(self.repo_dir, 'compile_commands.json',
                                    is_dir=False)

    def build(self):
        with pb.local.cwd(self.repo_dir):
            invoke(rustc, *self.build_flags)


def _parser_args():
    desc = 'Build and test examples.'
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument(
        '--only-examples', dest='regex_examples', type=regex, default='.*',
        help="Regular Expression to filter which example to build and run"
    )
    parser.add_argument('--deinit', default=False,
                        action='store_true', dest='deinit',
                        help='Deinitialize the submodules, this will remove\
                        all unstaged changes')
    c.add_args(parser)
    args = parser.parse_args()
    c.update_args(args)
    return args


def run(args):
    examples = [
        Genann(args),
        Grabc(args),
        Libxml2(args),
        Lil(args),
        Snudown(args),
        TinyCC(args),
        Tmux(args),
        Urlparser(args),
        Xzoom(args),
    ]
    for example in examples:
        if args.regex_examples.fullmatch(example.project_name):
            example.build_and_test()

    print(Colors.OKGREEN + "Done building and testing the examples." +
          Colors.NO_COLOR)


def main():
    setup_logging()
    args = _parser_args()
    run(args)


if __name__ == "__main__":
    main()
