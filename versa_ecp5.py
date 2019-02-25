#!/usr/bin/env python3

import sys
import argparse

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.build.generic_platform import *
from litex.boards.platforms import versa_ecp5

from litex.soc.cores.clock import *
from litex.soc.integration.soc_core import *
from litex.soc.integration.soc_sdram import *
from litex.soc.integration.builder import *
from litex.soc.cores.uart import UARTWishboneBridge
from litex.soc.interconnect import wishbone

from litedram.modules import MT41K64M16
from litedram.sdram_init import get_sdram_phy_py_header

from liteeth.common import *
from liteeth.core.mac import LiteEthMAC
from liteeth.core import LiteEthUDPIPCore

from litescope import LiteScopeAnalyzer

import ecp5ddrphy
import ecp5rgmii


class DDR3TestCRG(Module):
    def __init__(self, platform, sys_clk_freq):
        self.clock_domains.cd_init = ClockDomain()
        self.clock_domains.cd_por = ClockDomain(reset_less=True)
        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.cd_sys2x = ClockDomain()
        self.clock_domains.cd_sys2x_i = ClockDomain(reset_less=True)

        # # #

        self.stop = Signal()

        # clk / rst
        clk100 = platform.request("clk100")
        rst_n = platform.request("rst_n")
        platform.add_period_constraint(clk100, 10.0)

        # power on reset
        por_count = Signal(16, reset=2**16-1)
        por_done = Signal()
        self.comb += self.cd_por.clk.eq(ClockSignal())
        self.comb += por_done.eq(por_count == 0)
        self.sync.por += If(~por_done, por_count.eq(por_count - 1))

        # pll
        self.submodules.pll = pll = ECP5PLL()
        pll.register_clkin(clk100, 100e6)
        pll.create_clkout(self.cd_sys2x_i, 2*sys_clk_freq)
        pll.create_clkout(self.cd_init, 25e6)
        self.specials += [
            Instance("ECLKSYNCB",
                i_ECLKI=self.cd_sys2x_i.clk,
                i_STOP=self.stop,
                o_ECLKO=self.cd_sys2x.clk),
            Instance("CLKDIVF",
                p_DIV="2.0",
                i_ALIGNWD=0,
                i_CLKI=self.cd_sys2x.clk,
                i_RST=self.cd_sys2x.rst,
                o_CDIVX=self.cd_sys.clk),
            AsyncResetSynchronizer(self.cd_init, ~por_done | ~pll.locked | ~rst_n),
            AsyncResetSynchronizer(self.cd_sys, ~por_done | ~pll.locked | ~rst_n)
        ]


class DDR3TestSoC(SoCSDRAM):
    csr_map = {
        "ddrphy":    16,
        "analyzer":  17
    }
    csr_map.update(SoCSDRAM.csr_map)

    def __init__(self, toolchain="diamond"):
        platform = versa_ecp5.Platform(toolchain=toolchain)
        sys_clk_freq = int(50e6)
        SoCSDRAM.__init__(self, platform, clk_freq=sys_clk_freq,
                          cpu_type=None, l2_size=32,
                          with_uart=None,
                          csr_data_width=32,
                          ident="Versa ECP5 test SoC", ident_version=True)

        # crg
        crg = DDR3TestCRG(platform, sys_clk_freq)
        self.submodules.crg = crg

        # uart
        self.submodules.bridge = UARTWishboneBridge(platform.request("serial"), sys_clk_freq, baudrate=115200)
        self.add_wb_master(self.bridge.wishbone)

        # sdram
        self.submodules.ddrphy = ecp5ddrphy.ECP5DDRPHY(
            platform.request("ddram"),
            sys_clk_freq=sys_clk_freq)
        ddrphy_init = ecp5ddrphy.ECP5DDRPHYInit(self.crg, self.ddrphy)
        self.submodules += ddrphy_init
        sdram_module = MT41K64M16(sys_clk_freq, "1:2")
        self.register_sdram(self.ddrphy,
            sdram_module.geom_settings,
            sdram_module.timing_settings)

        # led blinking
        led_counter = Signal(32)
        self.sync += led_counter.eq(led_counter + 1)
        self.comb += platform.request("user_led", 0).eq(led_counter[26])

        # analyzer
        analyzer_signals = [
            self.ddrphy.dfi.p0,
            self.ddrphy.datavalid,
            self.ddrphy.burstdet,
            self.ddrphy.dqs_read,
            self.ddrphy.readposition
        ]
        analyzer_signals += [self.ddrphy.dq_i_data[i] for i in range(8)]
        self.submodules.analyzer = LiteScopeAnalyzer(analyzer_signals, 128)

    def do_exit(self, vns):
        if hasattr(self, "analyzer"):
            self.analyzer.export_csv(vns, "test/analyzer.csv")

class RGMIITestCRG(Module):
    def __init__(self, platform, sys_clk_freq):
        self.clock_domains.cd_por = ClockDomain(reset_less=True)
        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.cd_sys_i = ClockDomain(reset_less=True)

        # # #

        # clk / rst
        clk100 = platform.request("clk100")
        rst_n = platform.request("rst_n")
        platform.add_period_constraint(clk100, 10.0)

        # power on reset
        por_count = Signal(16, reset=2**16-1)
        por_done = Signal()
        self.comb += self.cd_por.clk.eq(ClockSignal())
        self.comb += por_done.eq(por_count == 0)
        self.sync.por += If(~por_done, por_count.eq(por_count - 1))

        # pll
        self.submodules.pll = pll = ECP5PLL()
        pll.register_clkin(clk100, 100e6)
        pll.create_clkout(self.cd_sys, sys_clk_freq)
        self.specials += AsyncResetSynchronizer(self.cd_sys, ~por_done | ~pll.locked | ~rst_n)


class RGMIITestSoC(SoCCore):
    def __init__(self, eth_port=0, toolchain="diamond"):
        platform = versa_ecp5.Platform(toolchain=toolchain)
        sys_clk_freq = int(133e6)
        SoCCore.__init__(self, platform, clk_freq=sys_clk_freq,
                          cpu_type=None, with_uart=False,
                          csr_data_width=32,
                          ident="Versa ECP5 test SoC", ident_version=True)

        # crg
        self.submodules.crg = RGMIITestCRG(platform, sys_clk_freq)

        # ethernet mac/udp/ip stack
        ethphy = ecp5rgmii.LiteEthPHYRGMII(platform.request("eth_clocks", eth_port),
                        platform.request("eth", eth_port))
        ethcore = LiteEthUDPIPCore(ethphy,
                                   mac_address=0x10e2d5000000,
                                   ip_address=convert_ip("192.168.7.50"),
                                   clk_freq=sys_clk_freq,
                                   with_icmp=True)
        self.submodules += ethphy, ethcore

        ethphy.crg.cd_eth_rx.clk.attr.add("keep")
        ethphy.crg.cd_eth_tx.clk.attr.add("keep")
        platform.add_period_constraint(ethphy.crg.cd_eth_rx.clk, period_ns(125e6))
        platform.add_period_constraint(ethphy.crg.cd_eth_tx.clk, period_ns(125e6))

        # led blinking
        led_counter = Signal(32)
        self.sync += led_counter.eq(led_counter + 1)
        self.comb += platform.request("user_led", 0).eq(led_counter[26])


class BaseSoC(SoCSDRAM):
    csr_map = {
        "ddrphy":    16,
    }
    csr_map.update(SoCSDRAM.csr_map)

    mem_map = {
        "firmware_ram": 0x20000000,
    }
    mem_map.update(SoCSDRAM.mem_map)
    def __init__(self, toolchain="diamond"):
        platform = versa_ecp5.Platform(toolchain=toolchain)
        sys_clk_freq = int(50e6)
        SoCSDRAM.__init__(self, platform, clk_freq=sys_clk_freq,
                          cpu_type="picorv32", l2_size=32,
                          integrated_rom_size=0x8000)

        # crg
        crg = DDR3TestCRG(platform, sys_clk_freq)
        self.submodules.crg = crg

        # firmware ram
        firmware_ram_size = 0x10000
        self.submodules.firmware_ram = wishbone.SRAM(firmware_ram_size)
        self.register_mem("firmware_ram", self.mem_map["firmware_ram"], self.firmware_ram.bus, firmware_ram_size)

        # sdram
        self.submodules.ddrphy = ecp5ddrphy.ECP5DDRPHY(
            platform.request("ddram"),
            sys_clk_freq=sys_clk_freq)
        ddrphy_init = ecp5ddrphy.ECP5DDRPHYInit(self.crg, self.ddrphy)
        self.submodules += ddrphy_init
        sdram_module = MT41K64M16(sys_clk_freq, "1:2")
        self.register_sdram(self.ddrphy,
            sdram_module.geom_settings,
            sdram_module.timing_settings)

        # led blinking
        led_counter = Signal(32)
        self.sync += led_counter.eq(led_counter + 1)
        self.comb += platform.request("user_led", 0).eq(led_counter[26])


class EthernetSoC(BaseSoC):
    csr_map = {
        "ethphy": 18,
        "ethmac": 19
    }
    csr_map.update(BaseSoC.csr_map)

    interrupt_map = {
        "ethmac": 3,
    }
    interrupt_map.update(BaseSoC.interrupt_map)

    mem_map = {
        "ethmac": 0x30000000,  # (shadow @0xb0000000)
    }
    mem_map.update(BaseSoC.mem_map)

    def __init__(self, **kwargs):
        BaseSoC.__init__(self, **kwargs)

        # ethernet mac
        self.submodules.ethphy = ecp5rgmii.LiteEthPHYRGMII(
            self.platform.request("eth_clocks"),
            self.platform.request("eth"))
        self.submodules.ethmac = LiteEthMAC(phy=self.ethphy, dw=32,
            interface="wishbone", endianness=self.cpu.endianness)
        self.add_wb_slave(mem_decoder(self.mem_map["ethmac"]), self.ethmac.bus)
        self.add_memory_region("ethmac", self.mem_map["ethmac"] | self.shadow_base, 0x2000)

        self.ethphy.crg.cd_eth_rx.clk.attr.add("keep")
        self.ethphy.crg.cd_eth_tx.clk.attr.add("keep")
        self.platform.add_period_constraint(self.ethphy.crg.cd_eth_rx.clk, period_ns(125e6))
        self.platform.add_period_constraint(self.ethphy.crg.cd_eth_tx.clk, period_ns(125e6))


def main():

    toolchain = "diamond"
    toolchain_path = "/usr/local/diamond/3.10_x64/bin/lin64"
    if "trellis" in sys.argv[1:]:
        toolchain = "trellis"
        toolchain_path = "/usr/share/trellis"


    if "ddr3_test" in sys.argv[1:]:
        soc = DDR3TestSoC(toolchain=toolchain)
    elif "rgmii_test" in sys.argv[1:]:
        soc = RGMIITestSoC(toolchain=toolchain)
    elif "base" in sys.argv[1:]:
        soc = BaseSoC(toolchain=toolchain)
    elif "ethernet" in sys.argv[1:]:
        soc = EthernetSoC(toolchain=toolchain)
    else:
        print("missing target, supported: (ddr3_test, rgmii_test, base, ethernet)")
        exit(1)
    builder = Builder(soc, output_dir="build", csr_csv="test/csr.csv")
    vns = builder.build(toolchain_path=toolchain_path)
    if isinstance(soc, DDR3TestSoC):
        soc.do_exit(vns)
        soc.generate_sdram_phy_py_header()

if __name__ == "__main__":
    main()
