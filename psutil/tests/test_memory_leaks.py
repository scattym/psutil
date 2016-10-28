#!/usr/bin/env python

# Copyright (c) 2009, Giampaolo Rodola'. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""
A test script which attempts to detect memory leaks by calling C
functions many times and compare process memory usage before and
after the calls.  It might produce false positives.
"""

import functools
import gc
import os
import socket
import threading
import time

import psutil
import psutil._common
from psutil import FREEBSD
from psutil import LINUX
from psutil import OPENBSD
from psutil import OSX
from psutil import POSIX
from psutil import SUNOS
from psutil import WINDOWS
from psutil._common import supports_ipv6
from psutil._compat import callable
from psutil._compat import xrange
from psutil.tests import get_test_subprocess
from psutil.tests import reap_children
from psutil.tests import RLIMIT_SUPPORT
from psutil.tests import run_test_module_by_name
from psutil.tests import safe_rmpath
from psutil.tests import TESTFN
from psutil.tests import TRAVIS
from psutil.tests import unittest


LOOPS = 1000
MEMORY_TOLERANCE = 4096
SKIP_PYTHON_IMPL = False
cext = psutil._psplatform.cext


# ===================================================================
# utils
# ===================================================================


def skip_if_linux():
    return unittest.skipIf(LINUX and SKIP_PYTHON_IMPL,
                           "worthless on LINUX (pure python)")


def bytes2human(n):
    """
    http://code.activestate.com/recipes/578019
    >>> bytes2human(10000)
    '9.8K'
    >>> bytes2human(100001221)
    '95.4M'
    """
    symbols = ('K', 'M', 'G', 'T', 'P', 'E', 'Z', 'Y')
    prefix = {}
    for i, s in enumerate(symbols):
        prefix[s] = 1 << (i + 1) * 10
    for s in reversed(symbols):
        if n >= prefix[s]:
            value = float(n) / prefix[s]
            return '%.1f%s' % (value, s)
    return "%sB" % n


class Base(unittest.TestCase):
    proc = psutil.Process()

    def setUp(self):
        gc.collect()

    def tearDown(self):
        reap_children()

    def execute(self, function, *args, **kwargs):
        def call_many_times():
            for x in xrange(LOOPS - 1):
                self.call(function, *args, **kwargs)
            del x
            gc.collect()
            return self.get_mem()

        self.call(function, *args, **kwargs)
        self.assertEqual(gc.garbage, [])
        self.assertEqual(threading.active_count(), 1)

        # RSS comparison
        # step 1
        rss1 = call_many_times()
        # step 2
        rss2 = call_many_times()

        difference = rss2 - rss1
        if difference > MEMORY_TOLERANCE:
            # This doesn't necessarily mean we have a leak yet.
            # At this point we assume that after having called the
            # function so many times the memory usage is stabilized
            # and if there are no leaks it should not increase any
            # more.
            # Let's keep calling fun for 3 more seconds and fail if
            # we notice any difference.
            stop_at = time.time() + 3
            while True:
                self.call(function, *args, **kwargs)
                if time.time() >= stop_at:
                    break
            del stop_at
            gc.collect()
            rss3 = self.get_mem()
            diff = rss3 - rss2
            if rss3 > rss2:
                self.fail("rss2=%s, rss3=%s, diff=%s (%s)"
                          % (rss2, rss3, diff, bytes2human(diff)))

    def execute_w_exc(self, exc, function, *args, **kwargs):
        kwargs['_exc'] = exc
        self.execute(function, *args, **kwargs)

    def get_mem(self):
        return psutil.Process().memory_info()[0]

    def call(self, function, *args, **kwargs):
        raise NotImplementedError("must be implemented in subclass")


# ===================================================================
# Process class
# ===================================================================


class TestProcessObjectLeaks(Base):
    """Test leaks of Process class methods."""

    def call(self, function, *args, **kwargs):
        if '_exc' in kwargs:
            exc = kwargs.pop('_exc')
            self.assertRaises(exc, function, *args, **kwargs)
        else:
            try:
                function(*args, **kwargs)
            except psutil.Error:
                pass

    @skip_if_linux()
    def test_name(self):
        self.execute(self.proc.name)

    @skip_if_linux()
    def test_cmdline(self):
        self.execute(self.proc.cmdline)

    @skip_if_linux()
    def test_exe(self):
        self.execute(self.proc.exe)

    @skip_if_linux()
    def test_ppid(self):
        self.execute(self.proc.ppid)

    @unittest.skipUnless(POSIX, "POSIX only")
    @skip_if_linux()
    def test_uids(self):
        self.execute(self.proc.uids)

    @unittest.skipUnless(POSIX, "POSIX only")
    @skip_if_linux()
    def test_gids(self):
        self.execute(self.proc.gids)

    @skip_if_linux()
    def test_status(self):
        self.execute(self.proc.status)

    def test_nice_get(self):
        self.execute(self.proc.nice)

    def test_nice_set(self):
        niceness = psutil.Process().nice()
        self.execute(self.proc.nice, niceness)

    @unittest.skipUnless(hasattr(psutil.Process, 'ionice'),
                         "platform not supported")
    def test_ionice_get(self):
        self.execute(self.proc.ionice)

    @unittest.skipUnless(hasattr(psutil.Process, 'ionice'),
                         "platform not supported")
    def test_ionice_set(self):
        if WINDOWS:
            value = psutil.Process().ionice()
            self.execute(self.proc.ionice, value)
        else:
            self.execute(self.proc.ionice, psutil.IOPRIO_CLASS_NONE)
            fun = functools.partial(cext.proc_ioprio_set, os.getpid(), -1, 0)
            self.execute_w_exc(OSError, fun)

    @unittest.skipIf(OSX or SUNOS, "platform not supported")
    @skip_if_linux()
    def test_io_counters(self):
        self.execute(self.proc.io_counters)

    @unittest.skipIf(POSIX, "worthless on POSIX")
    def test_username(self):
        self.execute(self.proc.username)

    @skip_if_linux()
    def test_create_time(self):
        self.execute(self.proc.create_time)

    @skip_if_linux()
    def test_num_threads(self):
        self.execute(self.proc.num_threads)

    @unittest.skipUnless(WINDOWS, "WINDOWS only")
    def test_num_handles(self):
        self.execute(self.proc.num_handles)

    @unittest.skipUnless(POSIX, "POSIX only")
    @skip_if_linux()
    def test_num_fds(self):
        self.execute(self.proc.num_fds)

    @skip_if_linux()
    def test_threads(self):
        self.execute(self.proc.threads)

    @skip_if_linux()
    def test_cpu_times(self):
        self.execute(self.proc.cpu_times)

    @skip_if_linux()
    def test_memory_info(self):
        self.execute(self.proc.memory_info)

    # also available on Linux but it's pure python
    @unittest.skipUnless(OSX or WINDOWS,
                         "platform not supported")
    def test_memory_full_info(self):
        self.execute(self.proc.memory_full_info)

    @unittest.skipUnless(POSIX, "POSIX only")
    @skip_if_linux()
    def test_terminal(self):
        self.execute(self.proc.terminal)

    @unittest.skipIf(POSIX and SKIP_PYTHON_IMPL,
                     "worthless on POSIX (pure python)")
    def test_resume(self):
        self.execute(self.proc.resume)

    @skip_if_linux()
    def test_cwd(self):
        self.execute(self.proc.cwd)

    @unittest.skipUnless(WINDOWS or LINUX or FREEBSD,
                         "platform not supported")
    def test_cpu_affinity_get(self):
        self.execute(self.proc.cpu_affinity)

    @unittest.skipUnless(WINDOWS or LINUX or FREEBSD,
                         "platform not supported")
    def test_cpu_affinity_set(self):
        affinity = psutil.Process().cpu_affinity()
        self.execute(self.proc.cpu_affinity, affinity)
        if not TRAVIS:
            self.execute_w_exc(ValueError, self.proc.cpu_affinity, [-1])

    @skip_if_linux()
    def test_open_files(self):
        safe_rmpath(TESTFN)  # needed after UNIX socket test has run
        with open(TESTFN, 'w'):
            self.execute(self.proc.open_files)

    # OSX implementation is unbelievably slow
    @unittest.skipIf(OSX, "too slow on OSX")
    @unittest.skipIf(OPENBSD, "platform not supported")
    @skip_if_linux()
    def test_memory_maps(self):
        self.execute(self.proc.memory_maps)

    @unittest.skipUnless(LINUX, "LINUX only")
    @unittest.skipUnless(LINUX and RLIMIT_SUPPORT, "LINUX >= 2.6.36 only")
    def test_rlimit_get(self):
        self.execute(self.proc.rlimit, psutil.RLIMIT_NOFILE)

    @unittest.skipUnless(LINUX, "LINUX only")
    @unittest.skipUnless(LINUX and RLIMIT_SUPPORT, "LINUX >= 2.6.36 only")
    def test_rlimit_set(self):
        limit = psutil.Process().rlimit(psutil.RLIMIT_NOFILE)
        self.execute(self.proc.rlimit, psutil.RLIMIT_NOFILE, limit)
        self.execute_w_exc(OSError, self.proc.rlimit, -1)

    @skip_if_linux()
    # Windows implementation is based on a single system-wide
    # function (tested later).
    @unittest.skipIf(WINDOWS, "worthless on WINDOWS")
    def test_connections(self):
        def create_socket(family, type):
            sock = socket.socket(family, type)
            sock.bind(('', 0))
            if type == socket.SOCK_STREAM:
                sock.listen(1)
            return sock

        socks = []
        socks.append(create_socket(socket.AF_INET, socket.SOCK_STREAM))
        socks.append(create_socket(socket.AF_INET, socket.SOCK_DGRAM))
        if supports_ipv6():
            socks.append(create_socket(socket.AF_INET6, socket.SOCK_STREAM))
            socks.append(create_socket(socket.AF_INET6, socket.SOCK_DGRAM))
        if hasattr(socket, 'AF_UNIX'):
            safe_rmpath(TESTFN)
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.bind(TESTFN)
            s.listen(1)
            socks.append(s)
        kind = 'all'
        # TODO: UNIX sockets are temporarily implemented by parsing
        # 'pfiles' cmd  output; we don't want that part of the code to
        # be executed.
        if SUNOS:
            kind = 'inet'
        try:
            self.execute(self.proc.connections, kind=kind)
        finally:
            for s in socks:
                s.close()

    @unittest.skipUnless(hasattr(psutil.Process, 'environ'),
                         "platform not supported")
    def test_environ(self):
        self.execute(self.proc.environ)

    @unittest.skipUnless(WINDOWS, "WINDOWS only")
    def test_proc_info(self):
        self.execute(cext.proc_info, os.getpid())


p = get_test_subprocess()
DEAD_PROC = psutil.Process(p.pid)
DEAD_PROC.kill()
DEAD_PROC.wait()
del p


class TestProcessObjectLeaksZombie(TestProcessObjectLeaks):
    """Same as above but looks for leaks occurring when dealing with
    zombie processes raising NoSuchProcess exception.
    """
    proc = DEAD_PROC

    def call(self, *args, **kwargs):
        try:
            TestProcessObjectLeaks.call(self, *args, **kwargs)
        except psutil.NoSuchProcess:
            pass

    if not POSIX:
        def test_kill(self):
            self.execute('kill')

        def test_terminate(self):
            self.execute('terminate')

        def test_suspend(self):
            self.execute('suspend')

        def test_resume(self):
            self.execute('resume')

        def test_wait(self):
            self.execute('wait')


# ===================================================================
# system APIs
# ===================================================================


class TestModuleFunctionsLeaks(Base):
    """Test leaks of psutil module functions."""

    def call(self, function, *args, **kwargs):
        fun = function if callable(function) else getattr(psutil, function)
        fun(*args, **kwargs)

    @skip_if_linux()
    def test_cpu_count_logical(self):
        self.execute('cpu_count', logical=True)

    @skip_if_linux()
    def test_cpu_count_physical(self):
        self.execute('cpu_count', logical=False)

    @skip_if_linux()
    def test_boot_time(self):
        self.execute('boot_time')

    @unittest.skipIf(POSIX and SKIP_PYTHON_IMPL,
                     "not worth being tested on POSIX (pure python)")
    def test_pid_exists(self):
        self.execute('pid_exists', os.getpid())

    def test_virtual_memory(self):
        self.execute('virtual_memory')

    # TODO: remove this skip when this gets fixed
    @unittest.skipIf(SUNOS,
                     "not worth being tested on SUNOS (uses a subprocess)")
    def test_swap_memory(self):
        self.execute('swap_memory')

    @skip_if_linux()
    def test_cpu_times(self):
        self.execute('cpu_times')

    @skip_if_linux()
    def test_per_cpu_times(self):
        self.execute('cpu_times', percpu=True)

    @unittest.skipIf(POSIX and SKIP_PYTHON_IMPL,
                     "not worth being tested on POSIX (pure python)")
    def test_disk_usage(self):
        self.execute('disk_usage', '.')

    def test_disk_partitions(self):
        self.execute('disk_partitions')

    @skip_if_linux()
    def test_net_io_counters(self):
        self.execute('net_io_counters')

    @unittest.skipIf(LINUX and not os.path.exists('/proc/diskstats'),
                     '/proc/diskstats not available on this Linux version')
    @skip_if_linux()
    def test_disk_io_counters(self):
        self.execute('disk_io_counters')

    # XXX - on Windows this produces a false positive
    @unittest.skipIf(WINDOWS, "XXX produces a false positive on Windows")
    def test_users(self):
        self.execute('users')

    @unittest.skipIf(LINUX,
                     "not worth being tested on Linux (pure python)")
    @unittest.skipIf(OSX and os.getuid() != 0, "need root access")
    def test_net_connections(self):
        self.execute('net_connections')

    def test_net_if_addrs(self):
        self.execute('net_if_addrs')

    @unittest.skipIf(TRAVIS, "EPERM on travis")
    def test_net_if_stats(self):
        self.execute('net_if_stats')

    def test_cpu_stats(self):
        self.execute('cpu_stats')

    if WINDOWS:

        def test_win_service_iter(self):
            self.execute(cext.winservice_enumerate)

        def test_win_service_get_config(self):
            name = next(psutil.win_service_iter()).name()
            self.execute(cext.winservice_query_config, name)

        def test_win_service_get_status(self):
            name = next(psutil.win_service_iter()).name()
            self.execute(cext.winservice_query_status, name)

        def test_win_service_get_description(self):
            name = next(psutil.win_service_iter()).name()
            self.execute(cext.winservice_query_descr, name)


if __name__ == '__main__':
    run_test_module_by_name(__file__)
