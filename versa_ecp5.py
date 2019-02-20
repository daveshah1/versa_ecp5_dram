#!/usr/bin/env python3

import argparse

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.build.generic_platform import *
from litex.boards.platforms import versa_ecp5

from litex.soc.cores.clock import *
from litex.soc.integration.soc_sdram import *
from litex.soc.integration.builder import *
from litex.soc.cores.uart import UARTWishboneBridge

from litescope import LiteScopeAnalyzer

from litedram.modules import MT41K64M16
from litedram.sdram_init import get_sdram_phy_py_header

import ecp5ddrphy


class _CRG(Module):
    def __init__(self, platform, sys_clk_freq):
        self.clock_domains.cd_startclk = ClockDomain()
        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.cd_sys2x = ClockDomain()
        self.clock_domains.cd_sys2x_i = ClockDomain(reset_less=True)

        # # #

        # clk / rst
        clk100 = platform.request("clk100")
        rst_n = platform.request("rst_n")
        platform.add_period_constraint(clk100, 10.0)

        uddcntln = Signal(reset=0b1)
        stop = Signal()
        dll_lock = Signal()
        freeze = Signal()
        pause = Signal()
        ddr_rst = Signal()
        # pll
        self.submodules.pll = pll = ECP5PLL()
        pll.register_clkin(clk100, 100e6)
        pll.create_clkout(self.cd_sys2x_i, 2*sys_clk_freq)
        pll.create_clkout(self.cd_startclk, 25e6)
        self.specials += Instance("ECLKSYNCB", i_ECLKI=self.cd_sys2x_i.clk, i_STOP=stop, o_ECLKO=self.cd_sys2x.clk)
        self.specials += Instance("CLKDIVF", p_DIV="2.0", i_CLKI=self.cd_sys2x.clk, i_RST=self.cd_sys2x.rst, i_ALIGNWD=0, o_CDIVX=self.cd_sys.clk)

        # Reset/init control
        self.specials += AsyncResetSynchronizer(self.cd_startclk, ~pll.locked | ~rst_n)
        self.specials += AsyncResetSynchronizer(self.cd_sys, ~pll.locked | ~rst_n)

        # Synchronise DDRDLL lock
        dll_lock_sync = Signal(2, reset=0b00)
        self.sync.startclk += dll_lock_sync.eq(Cat(dll_lock, dll_lock_sync[0]))

        # Reset & startup FSM
        init_timer = Signal(4, reset=0b0000)
        reset_ddr_done = Signal(reset=0b0)
        uddcntln_done = Signal(reset=0b0)

        fsm = ClockDomainsRenamer("startclk")(FSM(reset_state="WAIT_LOCK"))
        self.submodules += fsm
        fsm.act("WAIT_LOCK",
            uddcntln.eq(1), stop.eq(0), pause.eq(0),
            freeze.eq(0), ddr_rst.eq(0),
            If(dll_lock_sync[1],
                If(init_timer == 5,
                    NextValue(init_timer, 0),
                    If(uddcntln_done, NextState("READY"))
                    .Elif(reset_ddr_done, NextState("PAUSE"))
                    .Else(NextState("FREEZE"))
                ).Else(
                    NextValue(init_timer, init_timer + 1)
                )
            ).Else(
                NextValue(init_timer, 0)
            )
        )
        fsm.act("FREEZE",
            uddcntln.eq(1), stop.eq(0), pause.eq(0),
            freeze.eq(1), ddr_rst.eq(0),
            If(init_timer == 3,
                NextValue(init_timer, 0),
                If(reset_ddr_done, NextState("WAIT_LOCK"))
                .Else(NextState("STOP"))
            ).Else(
                NextValue(init_timer, init_timer + 1)
            )
        )
        fsm.act("STOP",
            uddcntln.eq(1), stop.eq(1), pause.eq(0),
            freeze.eq(1), ddr_rst.eq(0),
            If(init_timer == 3,
                NextValue(init_timer, 0),
                If(reset_ddr_done, NextState("FREEZE"))
                .Else(NextState("RESET_DDR"))
            ).Else(
                NextValue(init_timer, init_timer + 1)
            )
        )
        fsm.act("RESET_DDR",
            uddcntln.eq(1), stop.eq(1), pause.eq(0),
            freeze.eq(1), ddr_rst.eq(1),
            If(init_timer == 3,
                NextValue(init_timer, 0),
                NextValue(reset_ddr_done, 1),
                NextState("STOP")
            ).Else(
                NextValue(init_timer, init_timer + 1)
            )
        )
        fsm.act("PAUSE",
            uddcntln.eq(1), stop.eq(0), pause.eq(1),
            freeze.eq(0), ddr_rst.eq(0),
            If(init_timer == 3,
                NextValue(init_timer, 0),
                If(uddcntln_done, NextState("WAIT_LOCK"))
                .Else(NextState("UDDCNTLN"))
            ).Else(
                NextValue(init_timer, init_timer + 1)
            )
        )
        fsm.act("UDDCNTLN",
            uddcntln.eq(0), stop.eq(0), pause.eq(1),
            freeze.eq(0), ddr_rst.eq(0),
            If(init_timer == 3,
                NextValue(init_timer, 0),
                NextValue(uddcntln_done, 1),
                NextState("PAUSE")
            ).Else(
                NextValue(init_timer, init_timer + 1)
            )
        )
        fsm.act("READY",
            uddcntln.eq(1), stop.eq(0), pause.eq(0),
            freeze.eq(0), ddr_rst.eq(0)
        )
        self.pause = pause
        self.ddrdel = Signal()

        self.specials += Instance("DDRDLLA",
            i_CLK=self.cd_sys2x.clk,
            i_RST=self.cd_startclk.rst,
            i_UDDCNTLN=uddcntln,
            i_FREEZE=freeze,
            o_DDRDEL=self.ddrdel,
            o_LOCK=dll_lock
        )

        self.sync.startclk += self.cd_sys2x.rst.eq(ddr_rst)

class BaseSoC(SoCSDRAM):
    csr_map = {
        "ddrphy":    16,
        "analyzer":  17
    }
    csr_map.update(SoCSDRAM.csr_map)
    def __init__(self, with_cpu=False, **kwargs):
        platform = versa_ecp5.Platform(toolchain="diamond")
        sys_clk_freq = int(50e6)
        SoCSDRAM.__init__(self, platform, clk_freq=sys_clk_freq,
                          cpu_type="picorv32" if with_cpu else None, l2_size=32,
                          integrated_rom_size=0x8000 if with_cpu else 0,
                          with_uart=with_cpu,
                          csr_data_width=8 if with_cpu else 32,
                          ident="Versa ECP5 test SoC", ident_version=True,
                          **kwargs)

        # crg
        crg = _CRG(platform, sys_clk_freq)
        self.submodules.crg = crg

        # uart
        if not with_cpu:
            self.submodules.bridge = UARTWishboneBridge(platform.request("serial"), sys_clk_freq, baudrate=115200)
            self.add_wb_master(self.bridge.wishbone)

        # sdram
        self.submodules.ddrphy = ecp5ddrphy.ECP5DDRPHY(
            platform.request("ddram"),
            pause=crg.pause, ddrdel=crg.ddrdel,
            sys_clk_freq=sys_clk_freq)
        sdram_module = MT41K64M16(sys_clk_freq, "1:2")
        self.register_sdram(self.ddrphy,
            sdram_module.geom_settings,
            sdram_module.timing_settings)
        self.add_constant("MEMTEST_BUS_DEBUG", None)
        self.add_constant("MEMTEST_DATA_SIZE", 1024)
        self.add_constant("MEMTEST_DATA_DEBUG", None)
        self.add_constant("MEMTEST_ADDR_SIZE", 1024)
        self.add_constant("MEMTEST_ADDR_DEBUG", None)

        # led blinking
        led_counter = Signal(32)
        self.sync += led_counter.eq(led_counter + 1)
        self.comb += platform.request("user_led", 0).eq(led_counter[26])

        # analyzer
        if not with_cpu:
            analyzer_signals = [
                self.ddrphy.dfi.p0,
                self.ddrphy.datavalid,
                self.ddrphy.burstdet,
                self.ddrphy.dqs_read,
                self.ddrphy.readposition
            ]
            analyzer_signals += [self.ddrphy.dq_i_data[i] for i in range(8)]
            self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals, 128)

    def generate_sdram_phy_py_header(self):
        f = open("test/sdram_init.py", "w")
        f.write(get_sdram_phy_py_header(
            self.sdram.controller.settings.phy,
            self.sdram.controller.settings.timing))
        f.close()

    def do_exit(self, vns):
        if hasattr(self, "analyzer"):
            self.analyzer.export_csv(vns, "test/analyzer.csv")

def main():
    parser = argparse.ArgumentParser(description="LiteX SoC port to the Versa ECP5")
    builder_args(parser)
    soc_sdram_args(parser)
    args = parser.parse_args()

    soc = BaseSoC(**soc_sdram_argdict(args))
    builder = Builder(soc, output_dir="build", csr_csv="test/csr.csv")
    vns = builder.build(toolchain_path="/usr/local/diamond/3.10_x64/bin/lin64")
    soc.do_exit(vns)
    soc.generate_sdram_phy_py_header()


if __name__ == "__main__":
    main()
