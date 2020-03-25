# This file is Copyright (c) 2016-2018 Florent Kermarrec <florent@enjoy-digital.fr>
# This file is Copyright (c) 2016 Tim 'mithro' Ansell <mithro@mithis.com>
# This file is Copyright (c) 2020 Antmicro <www.antmicro.com>
# License: BSD

import unittest

from migen import *

from litex.gen.sim import *

from litedram.common import *
from litedram.frontend.bist import *
from litedram.frontend.bist import _LiteDRAMBISTGenerator, _LiteDRAMBISTChecker, \
    _LiteDRAMPatternGenerator, _LiteDRAMPatternChecker

from test.common import *


class GenCheckDriver:
    def __init__(self, module):
        self.module = module

    def reset(self):
        yield self.module.reset.eq(1)
        yield
        yield self.module.reset.eq(0)
        yield

    def configure(self, base, length, end=None, random_addr=None, random_data=None):
        # for non-pattern generators/checkers
        if end is None:
            end = base + 0x100000
        yield self.module.base.eq(base)
        yield self.module.end.eq(end)
        yield self.module.length.eq(length)
        if random_addr is not None:
            yield self.module.random_addr.eq(random_addr)
        if random_data is not None:
            yield self.module.random_data.eq(random_data)

    def run(self):
        yield self.module.run.eq(1)
        yield self.module.start.eq(1)
        yield
        yield self.module.start.eq(0)
        yield
        while((yield self.module.done) == 0):
            yield
        if hasattr(self.module, "errors"):
            self.errors = (yield self.module.errors)


class GenCheckCSRDriver:
    def __init__(self, module):
        self.module = module

    def reset(self):
        yield from self.module.reset.write(1)
        yield from self.module.reset.write(0)

    def configure(self, base, length, end=None, random_addr=None, random_data=None):
        # for non-pattern generators/checkers
        if end is None:
            end = base + 0x100000
        yield from self.module.base.write(base)
        yield from self.module.end.write(end)
        yield from self.module.length.write(length)
        if random_addr is not None:
            yield from self.module.random.addr.write(random_addr)
        if random_data is not None:
            yield from self.module.random.data.write(random_data)

    def run(self):
        yield from self.module.run.write(1)
        yield from self.module.start.write(1)
        yield
        yield from self.module.start.write(0)
        yield
        while((yield from self.module.done.read()) == 0):
            yield
        if hasattr(self.module, "errors"):
            self.errors = (yield from self.module.errors.read())


class TestBIST(MemoryTestDataMixin, unittest.TestCase):

    # Generator ------------------------------------------------------------------------------------

    def test_generator(self):
        def main_generator(dut):
            self.errors = 0

            # test incr
            yield dut.ce.eq(1)
            yield dut.random_enable.eq(0)
            yield
            for i in range(1024):
                data = (yield dut.o)
                if data != i:
                    self.errors += 1
                yield

            # test random
            datas = []
            yield dut.ce.eq(1)
            yield dut.random_enable.eq(1)
            for i in range(1024):
                data = (yield dut.o)
                if data in datas:
                    self.errors += 1
                datas.append(data)
                yield

        # dut
        dut = Generator(23, n_state=23, taps=[17, 22])

        # simulation
        generators = [main_generator(dut)]
        run_simulation(dut, generators)
        self.assertEqual(self.errors, 0)

    def generator_test(self, mem_expected, data_width, pattern=None, config_args=None,
                       check_mem=True):
        assert pattern is None or config_args is None, \
            "_LiteDRAMBISTGenerator xor _LiteDRAMPatternGenerator"

        class DUT(Module):
            def __init__(self):
                self.write_port = LiteDRAMNativeWritePort(address_width=32, data_width=data_width)
                if pattern is not None:
                    self.submodules.generator = _LiteDRAMPatternGenerator(self.write_port, pattern)
                else:
                    self.submodules.generator = _LiteDRAMBISTGenerator(self.write_port)
                self.mem = DRAMMemory(data_width, len(mem_expected))

        def main_generator(driver):
            yield from driver.reset()
            if pattern is None:
                yield from driver.configure(**config_args)
            yield from driver.run()
            yield

        dut = DUT()
        generators = [
            main_generator(GenCheckDriver(dut.generator)),
            dut.mem.write_handler(dut.write_port),
        ]
        run_simulation(dut, generators)
        if check_mem:
            self.assertEqual(dut.mem.mem, mem_expected)
        return dut

    # _LiteDRAMBISTGenerator -----------------------------------------------------------------------

    def test_bist_generator_8bit(self):
        data = self.bist_test_data["8bit"]
        self.generator_test(data.pop("expected"), data_width=8, config_args=data)

    def test_bist_generator_range_must_be_pow2(self):
        # NOTE:
        # in the current implementation (end - start) must be a power of 2,
        # but it would be better if this restriction didn't hold, this test
        # is here just to notice the change if it happens unintentionally
        # and may be removed if we start supporting arbitrary ranges
        data = self.bist_test_data["8bit"]
        data["end"] += 1
        reference = data.pop("expected")
        dut = self.generator_test(reference, data_width=8, config_args=data, check_mem=False)
        self.assertNotEqual(dut.mem.mem, reference)

    def test_bist_generator_32bit(self):
        data = self.bist_test_data["32bit"]
        self.generator_test(data.pop("expected"), data_width=32, config_args=data)

    def test_bist_generator_64bit(self):
        data = self.bist_test_data["64bit"]
        self.generator_test(data.pop("expected"), data_width=64, config_args=data)

    def test_bist_generator_32bit_address_masked(self):
        data = self.bist_test_data["32bit_masked"]
        self.generator_test(data.pop("expected"), data_width=32, config_args=data)

    def test_bist_generator_32bit_long_sequential(self):
        data = self.bist_test_data["32bit_long_sequential"]
        self.generator_test(data.pop("expected"), data_width=32, config_args=data)

    def test_bist_generator_random_data(self):
        data = self.bist_test_data["32bit"]
        data["random_data"] = True
        dut = self.generator_test(data.pop("expected"), data_width=32, config_args=data,
                                  check_mem=False)
        # only check that there are no duplicates and that data is not a simple sequence
        mem = [val for val in dut.mem.mem if val != 0]
        self.assertEqual(len(set(mem)), len(mem), msg="Duplicate values in memory")
        self.assertNotEqual(mem, list(range(len(mem))), msg="Values are a sequence")

    def test_bist_generator_random_addr(self):
        data = self.bist_test_data["32bit"]
        data["random_addr"] = True
        dut = self.generator_test(data.pop("expected"), data_width=32, config_args=data,
                                  check_mem=False)
        # with random address and address wrapping (generator.end) we _can_ have duplicates
        # we can at least check that the values written are not an ordered sequence
        mem = [val for val in dut.mem.mem if val != 0]
        self.assertNotEqual(mem, list(range(len(mem))), msg="Values are a sequence")
        self.assertLess(max(mem), data["length"], msg="Too big value found")

    # _LiteDRAMPatternGenerator --------------------------------------------------------------------

    def test_pattern_generator_8bit(self):
        data = self.pattern_test_data["8bit"]
        self.generator_test(data["expected"], data_width=8, pattern=data["pattern"])

    def test_pattern_generator_32bit(self):
        data = self.pattern_test_data["32bit"]
        self.generator_test(data["expected"], data_width=32, pattern=data["pattern"])

    def test_pattern_generator_64bit(self):
        data = self.pattern_test_data["64bit"]
        self.generator_test(data["expected"], data_width=64, pattern=data["pattern"])

    def test_pattern_generator_32bit_not_aligned(self):
        data = self.pattern_test_data["32bit_not_aligned"]
        self.generator_test(data["expected"], data_width=32, pattern=data["pattern"])

    def test_pattern_generator_32bit_duplicates(self):
        data = self.pattern_test_data["32bit_duplicates"]
        self.generator_test(data["expected"], data_width=32, pattern=data["pattern"])

    def test_pattern_generator_32bit_sequential(self):
        data = self.pattern_test_data["32bit_sequential"]
        self.generator_test(data["expected"], data_width=32, pattern=data["pattern"])

    # _LiteDRAMBISTChecker -------------------------------------------------------------------------

    def checker_test(self, memory, data_width, pattern=None, config_args=None, check_errors=False):
        assert pattern is None or config_args is None, \
            "_LiteDRAMBISTChecker xor _LiteDRAMPatternChecker"

        class DUT(Module):
            def __init__(self):
                self.read_port = LiteDRAMNativeReadPort(address_width=32, data_width=data_width)
                if pattern is not None:
                    self.submodules.checker = _LiteDRAMPatternChecker(self.read_port, init=pattern)
                else:
                    self.submodules.checker = _LiteDRAMBISTChecker(self.read_port)
                self.mem = DRAMMemory(data_width, len(memory), init=memory)

        def main_generator(driver):
            yield from driver.reset()
            if pattern is None:
                yield from driver.configure(**config_args)
            yield from driver.run()
            yield

        dut = DUT()
        checker = GenCheckDriver(dut.checker)
        generators = [
            main_generator(checker),
            dut.mem.read_handler(dut.read_port),
        ]
        run_simulation(dut, generators)
        if check_errors:
            self.assertEqual(checker.errors, 0)
        return dut, checker

    def test_bist_checker_8bit(self):
        data = self.bist_test_data["8bit"]
        memory = data.pop("expected")
        self.checker_test(memory, data_width=8, config_args=data)

    def test_bist_checker_32bit(self):
        data = self.bist_test_data["32bit"]
        memory = data.pop("expected")
        self.checker_test(memory, data_width=32, config_args=data)

    def test_bist_checker_64bit(self):
        data = self.bist_test_data["32bit"]
        memory = data.pop("expected")
        self.checker_test(memory, data_width=32, config_args=data)

    # _LiteDRAMPatternChecker ----------------------------------------------------------------------

    def test_pattern_checker_8bit(self):
        data = self.pattern_test_data["8bit"]
        self.checker_test(memory=data["expected"], data_width=8, pattern=data["pattern"])

    def test_pattern_checker_32bit(self):
        data = self.pattern_test_data["32bit"]
        self.checker_test(memory=data["expected"], data_width=32, pattern=data["pattern"])

    def test_pattern_checker_64bit(self):
        data = self.pattern_test_data["64bit"]
        self.checker_test(memory=data["expected"], data_width=64, pattern=data["pattern"])

    def test_pattern_checker_32bit_not_aligned(self):
        data = self.pattern_test_data["32bit_not_aligned"]
        self.checker_test(memory=data["expected"], data_width=32, pattern=data["pattern"])

    def test_pattern_checker_32bit_duplicates(self):
        data = self.pattern_test_data["32bit_duplicates"]
        num_duplicates = len(data["pattern"]) - len(set(adr for adr, _ in data["pattern"]))
        dut, checker = self.checker_test(
            memory=data["expected"], data_width=32, pattern=data["pattern"], check_errors=False)
        self.assertEqual(checker.errors, num_duplicates)

    # LiteDRAMBISTGenerator and LiteDRAMBISTChecker ------------------------------------------------

    def bist_test(self, generator, checker, mem):
        # write
        yield from generator.reset()
        yield from generator.configure(16, 64)
        yield from generator.run()

        # read (no errors)
        yield from checker.reset()
        yield from checker.configure(16, 64)
        yield from checker.run()
        self.assertEqual(checker.errors, 0)

        # corrupt memory (using generator)
        yield from generator.reset()
        yield from generator.configure(16 + 48, 64)
        yield from generator.run()

        # read (errors)
        yield from checker.reset()
        yield from checker.configure(16, 64)
        yield from checker.run()
        # errors for words:
        # from (16 + 48) / 4 = 16  (corrupting generator start)
        # to   (16 + 64) / 4 = 20  (first generator end)
        self.assertEqual(checker.errors, 4)

        # read (no errors)
        yield from checker.reset()
        yield from checker.configure(16 + 48, 64)
        yield from checker.run()
        self.assertEqual(checker.errors, 0)

    def test_bist_base(self):
        class DUT(Module):
            def __init__(self):
                self.write_port = LiteDRAMNativeWritePort(address_width=32, data_width=32)
                self.read_port = LiteDRAMNativeReadPort(address_width=32, data_width=32)
                self.submodules.generator = _LiteDRAMBISTGenerator(self.write_port)
                self.submodules.checker = _LiteDRAMBISTChecker(self.read_port)

        def main_generator(dut, mem):
            generator = GenCheckDriver(dut.generator)
            checker = GenCheckDriver(dut.checker)
            yield from self.bist_test(generator, checker, mem)

        # dut
        dut = DUT()
        mem = DRAMMemory(32, 48)

        # simulation
        generators = [
            main_generator(dut, mem),
            mem.write_handler(dut.write_port),
            mem.read_handler(dut.read_port)
        ]
        run_simulation(dut, generators)

    def test_bist_csr(self):
        class DUT(Module):
            def __init__(self):
                self.write_port = LiteDRAMNativeWritePort(address_width=32, data_width=32)
                self.read_port = LiteDRAMNativeReadPort(address_width=32, data_width=32)
                self.submodules.generator = LiteDRAMBISTGenerator(self.write_port)
                self.submodules.checker = LiteDRAMBISTChecker(self.read_port)

        def main_generator(dut, mem):
            generator = GenCheckCSRDriver(dut.generator)
            checker = GenCheckCSRDriver(dut.checker)
            yield from self.bist_test(generator, checker, mem)

        # dut
        dut = DUT()
        mem = DRAMMemory(32, 48)

        # simulation
        generators = [
            main_generator(dut, mem),
            mem.write_handler(dut.write_port),
            mem.read_handler(dut.read_port)
        ]
        run_simulation(dut, generators)

    # FIXME: synchronization between CSRs: `start` and `base`, `done` and `errors`
    #  def test_bist_csr_cdc(self):
    #      class DUT(Module):
    #          def __init__(self):
    #              port_kwargs = dict(address_width=32, data_width=32, clock_domain="async")
    #              self.write_port = LiteDRAMNativeWritePort(**port_kwargs)
    #              self.read_port = LiteDRAMNativeReadPort(**port_kwargs)
    #              self.submodules.generator = LiteDRAMBISTGenerator(self.write_port)
    #              self.submodules.checker = LiteDRAMBISTChecker(self.read_port)
    #
    #      def main_generator(dut, mem):
    #          generator = GenCheckCSRDriver(dut.generator)
    #          checker = GenCheckCSRDriver(dut.checker)
    #          yield from self.bist_test(generator, checker, mem)
    #
    #      # dut
    #      dut = DUT()
    #      mem = DRAMMemory(32, 48)
    #
    #      generators = {
    #          "sys": [
    #              main_generator(dut, mem),
    #          ],
    #          "async": [
    #              mem.write_handler(dut.write_port),
    #              mem.read_handler(dut.read_port)
    #          ]
    #      }
    #      clocks = {
    #          "sys": 10,
    #          "async": (7, 3),
    #      }
    #      run_simulation(dut, generators, clocks)
