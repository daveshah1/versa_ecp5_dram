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

from litedram.sdram_init import get_sdram_phy_py_header

import ecp5ddrphy
from litedram.modules import SDRAMModule, _TechnologyTimings, _SpeedgradeTimings

_ddram_io = [
    ("ddram", 0,
        Subsignal("a", Pins(
            "P2 C4 E5 F5 B3 F4 B5 E4",
            "C5 E3 D5 B4 C3"),
            IOStandard("SSTL135_I")),
        Subsignal("ba", Pins("P5 N3 M3"), IOStandard("SSTL135_I")),
        Subsignal("ras_n", Pins("P1"), IOStandard("SSTL135_I")),
        Subsignal("cas_n", Pins("L1"), IOStandard("SSTL135_I")),
        Subsignal("we_n", Pins("M1"), IOStandard("SSTL135_I")),
        Subsignal("cs_n", Pins("K1"), IOStandard("SSTL135_I")),
        Subsignal("dm", Pins("J4 H5"), IOStandard("SSTL135_I")),
        Subsignal("dq", Pins(
            "L5 F1 K4 G1 L4 H1 G2 J3",
            "D1 C1 E2 C2 F3 A2 E1 B1"),
            IOStandard("SSTL135_I"),
            Misc("TERMINATION=75")),
        Subsignal("dqs_p", Pins("K2 H4"), IOStandard("SSTL135D_I"), Misc("TERMINATION=OFF"), Misc("DIFFRESISTOR=100")),
        #Subsignal("dqs_n", Pins("J1 G5"), IOStandard("SSTL135D_I")),
        Subsignal("clk_p", Pins("M4"), IOStandard("SSTL135D_I")),
        #Subsignal("clk_n", Pins("N5"), IOStandard("SSTL135D_I")),
        Subsignal("cke", Pins("N2"), IOStandard("SSTL135_I")),
        Subsignal("odt", Pins("L2"), IOStandard("SSTL135_I")),
        Subsignal("reset_n", Pins("N4"), IOStandard("SSTL135_I")),
        Misc("SLEWRATE=FAST"),
    )
]

class MT41K64M16(SDRAMModule):
    memtype = "DDR3"
    # geometry
    nbanks = 8
    nrows  = 8192
    ncols  = 1024
    # timings
    technology_timings = _TechnologyTimings(tREFI=64e6/8192, tWTR=(4, 7.5), tCCD=(4, None), tRRD=(4, 10))
    speedgrade_timings = {
        "800": _SpeedgradeTimings(tRP=13.1, tRCD=13.1, tWR=13.1, tRFC=64, tFAW=(None, 50), tRAS=37.5),
        "1066": _SpeedgradeTimings(tRP=13.1, tRCD=13.1, tWR=13.1, tRFC=86, tFAW=(None, 50), tRAS=37.5),
        "1333": _SpeedgradeTimings(tRP=13.5, tRCD=13.5, tWR=13.5, tRFC=107, tFAW=(None, 45), tRAS=36),
        "1600": _SpeedgradeTimings(tRP=13.75, tRCD=13.75, tWR=13.75, tRFC=128, tFAW=(None, 40), tRAS=35),
    }
    speedgrade_timings["default"] = speedgrade_timings["1600"]


class _CRG(Module):
    def __init__(self, platform, sys_clk_freq):
        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.cd_sys2x = ClockDomain(reset_less=True)
        self.clock_domains.cd_sys2x_i = ClockDomain(reset_less=True)

        # # #

        # clk / rst
        clk100 = platform.request("clk100")
        rst_n = platform.request("rst_n")
        platform.add_period_constraint(clk100, 20.0)

        # pll
        self.submodules.pll = pll = ECP5PLL()
        pll.register_clkin(clk100, 100e6)
        pll.create_clkout(self.cd_sys2x_i, 2*sys_clk_freq)
        self.specials += Instance("ECLKSYNCB", i_ECLKI=self.cd_sys2x_i.clk, i_STOP=0, o_ECLKO=self.cd_sys2x.clk)
        self.specials += Instance("CLKDIVF", p_DIV="2.0", i_CLKI=self.cd_sys2x.clk, i_RST=0, i_ALIGNWD=0, o_CDIVX=self.cd_sys.clk)
        self.specials += AsyncResetSynchronizer(self.cd_sys, ~pll.locked | ~rst_n)

class BaseSoC(SoCSDRAM):
    csr_map = {
        "ddrphy":    16,
        "analyzer":  17
    }
    csr_map.update(SoCSDRAM.csr_map)
    def __init__(self, with_cpu=False, **kwargs):
        platform = versa_ecp5.Platform(toolchain="trellis")
        platform.add_extension(_ddram_io)
        sys_clk_freq = int(50e6)
        SoCSDRAM.__init__(self, platform, clk_freq=sys_clk_freq,
                          cpu_type="picorv32" if with_cpu else None, l2_size=32,
                          integrated_rom_size=0x8000 if with_cpu else 0,
                          with_uart=with_cpu,
                          csr_data_width=8 if with_cpu else 32,
                          ident="Versa ECP5 test SoC", ident_version=True,
                          **kwargs)

        # crg
        self.submodules.crg = _CRG(platform, sys_clk_freq)

        # uart
        if not with_cpu:
            self.submodules.bridge = UARTWishboneBridge(platform.request("serial"), sys_clk_freq, baudrate=115200)
            self.add_wb_master(self.bridge.wishbone)

        # sdram
        self.submodules.ddrphy = ecp5ddrphy.ECP5DDRPHY(
            platform.request("ddram"),
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
    vns = builder.build(toolchain_path="/usr/share/trellis")
    soc.do_exit(vns)
    soc.generate_sdram_phy_py_header()


if __name__ == "__main__":
    main()
