# 1:2 frequency-ratio DDR3 PHY for Lattice's ECP5
# DDR3: 800 MT/s

import math
from collections import OrderedDict

from migen import *
from migen.genlib.misc import BitSlip
from migen.fhdl.specials import Tristate

from litex.soc.interconnect.csr import *

from litedram.common import PhySettings
from litedram.phy.dfi import *

# Helpers ------------------------------------------------------------------------------------------

def get_cl_cw(memtype, tck):
    f_to_cl_cwl = OrderedDict()
    if memtype == "DDR3":
        f_to_cl_cwl[800e6]  = (6, 5)
    else:
        raise ValueError
    for f, (cl, cwl) in f_to_cl_cwl.items():
        if tck >= 2/f:
            return cl, cwl
    raise ValueError

def get_sys_latency(nphases, cas_latency):
    return math.ceil(cas_latency/nphases)

def get_sys_phases(nphases, sys_latency, cas_latency):
    dat_phase = sys_latency*nphases - cas_latency
    cmd_phase = (dat_phase - 1)%nphases
    return cmd_phase, dat_phase

# Lattice ECP5 DDR PHY -----------------------------------------------------------------------------

class ECP5DDRPHY(Module, AutoCSR):
    def __init__(self, pads, sys_clk_freq=100e6):
        memtype = "DDR3"
        tck = 2/(2*2*sys_clk_freq)
        addressbits = len(pads.a)
        bankbits = len(pads.ba)
        nranks = 1 if not hasattr(pads, "cs_n") else len(pads.cs_n)
        databits = len(pads.dq)
        nphases = 2

        # Registers --------------------------------------------------------------------------------

        self._half_sys8x_taps = CSRStorage(4, reset=0) # FIXME

        self._wlevel_en = CSRStorage()
        self._wlevel_strobe = CSR()

        self._dly_sel = CSRStorage(databits//8)

        self._rdly_dq_rst = CSR()
        self._rdly_dq_inc = CSR()
        self._rdly_dq_bitslip_rst = CSR()
        self._rdly_dq_bitslip = CSR()

        self._wdly_dq_rst = CSR()
        self._wdly_dq_inc = CSR()
        self._wdly_dqs_rst = CSR()
        self._wdly_dqs_inc = CSR()

        # PHY settings -----------------------------------------------------------------------------
        cl, cwl = get_cl_cw(memtype, tck)
        cl_sys_latency = get_sys_latency(nphases, cl)
        cwl_sys_latency = get_sys_latency(nphases, cwl)

        rdcmdphase, rdphase = get_sys_phases(nphases, cl_sys_latency, cl)
        wrcmdphase, wrphase = get_sys_phases(nphases, cwl_sys_latency, cwl)
        self.settings = PhySettings(
            memtype=memtype,
            dfi_databits=4*databits,
            nranks=nranks,
            nphases=nphases,
            rdphase=rdphase,
            wrphase=wrphase,
            rdcmdphase=rdcmdphase,
            wrcmdphase=wrcmdphase,
            cl=cl,
            cwl=cwl,
            read_latency=2 + cl_sys_latency + 2 + log2_int(4//nphases) + 3, # FIXME
            write_latency=cwl_sys_latency
        )

        # DFI Interface ----------------------------------------------------------------------------
        self.dfi = Interface(addressbits, bankbits, nranks, 4*databits, 4)

        # # #

        bl8_sel = Signal()

        # Clock ------------------------------------------------------------------------------------
        for i in range(len(pads.clk_p)):
            sd_clk_se = Signal()
            self.specials += [
                Instance("ODDRX2F",
                    i_D0=0,
                    i_D1=1,
                    i_D2=0,
                    i_D3=1,
                    i_ECLK=ClockSignal("sys2x"),
                    i_SCLK=ClockSignal(),
                    i_RST=ResetSignal(),
                    o_Q=pads.clk_p[i]
                ),
            ]

        # Addresses and Commands -------------------------------------------------------------------
        for i in range(addressbits):
            self.specials += \
                Instance("ODDRX2F",
                    i_D0=self.dfi.phases[0].address[i],
                    i_D1=self.dfi.phases[0].address[i],
                    i_D2=self.dfi.phases[1].address[i],
                    i_D3=self.dfi.phases[1].address[i],
                    i_ECLK=ClockSignal("sys2x"),
                    i_SCLK=ClockSignal(),
                    i_RST=ResetSignal(),
                    o_Q=pads.a[i]
                )
        for i in range(bankbits):
            self.specials += \
                 Instance("ODDRX2F",
                    i_D0=self.dfi.phases[0].bank[i],
                    i_D1=self.dfi.phases[0].bank[i],
                    i_D2=self.dfi.phases[1].bank[i],
                    i_D3=self.dfi.phases[1].bank[i],
                    i_ECLK=ClockSignal("sys2x"),
                    i_SCLK=ClockSignal(),
                    i_RST=ResetSignal(),
                    o_Q=pads.ba[i]
                )
        controls = ["ras_n", "cas_n", "we_n", "cke", "odt"]
        if hasattr(pads, "reset_n"):
            controls.append("reset_n")
        if hasattr(pads, "cs_n"):
            controls.append("cs_n")
        for name in controls:
            for i in range(len(getattr(pads, name))):
                self.specials += \
                    Instance("ODDRX2F",
                        i_D0=getattr(self.dfi.phases[0], name)[i],
                        i_D1=getattr(self.dfi.phases[0], name)[i],
                        i_D2=getattr(self.dfi.phases[1], name)[i],
                        i_D3=getattr(self.dfi.phases[1], name)[i],
                        i_ECLK=ClockSignal("sys2x"),
                        i_SCLK=ClockSignal(),
                        i_RST=ResetSignal(),
                        o_Q=getattr(pads, name)[i]
                )

        # DQ ---------------------------------------------------------------------------------------
        oe_dq = Signal()
        oe_dqs = Signal()
        for i in range(databits//8):
            # DQSBUFM
            dqsr90  = Signal()
            dqsw270 = Signal()
            dqsw    = Signal()
            rdpntr  = Signal(3)
            wrpntr  = Signal(3)
            self.specials += Instance("DQSBUFM",
                # Clocks / Reset
                i_SCLK=ClockSignal("sys"),
                i_ECLK=ClockSignal("sys2x"),
                i_RST=ResetSignal(),

                # Control
                i_RDLOADN=~(self._dly_sel.storage[i//8] & self._rdly_dq_rst.re),
                i_RDMOVE=self._dly_sel.storage[i//8] & self._rdly_dq_inc.re,
                i_RDDIRECTION=1,
                i_WRLOADN=~(self._dly_sel.storage[i//8] & self._wdly_dq_rst.re),
                i_WRMOVE=self._dly_sel.storage[i//8] & self._wdly_dq_inc.re,
                i_WRDIRECTION=1,

                # Reads (generate shifted DQS clock for reads)
                i_READ0=1,
                i_READ1=1,
                i_DQSI=pads.dqs_p[i],
                o_DQSR90=dqsr90,
                o_RDPNTR0=rdpntr[0],
                o_RDPNTR1=rdpntr[1],
                o_RDPNTR2=rdpntr[2],
                o_WRPNTR0=wrpntr[0],
                o_WRPNTR1=wrpntr[1],
                o_WRPNTR2=wrpntr[2],

                # Writes (generate shifted ECLK clock for writes)
                o_DQSW270=dqsw270,
                o_DQSW=dqsw
            )

            # DQS and DM ---------------------------------------------------------------------------
            dqs_serdes_pattern = Signal(8, reset=0b0101)
            self.comb += \
                If(self._wlevel_en.storage,
                    If(self._wlevel_strobe.re,
                        dqs_serdes_pattern.eq(0b0001)
                    ).Else(
                        dqs_serdes_pattern.eq(0b0000)
                    )
                 ).Else(
                    dqs_serdes_pattern.eq(0b0101)
                )

            dm_data = Signal(8)
            dm_data_d = Signal(8)
            dm_data_muxed = Signal(4)
            self.comb += dm_data.eq(Cat(
                self.dfi.phases[0].wrdata_mask[0*databits//8+i], self.dfi.phases[0].wrdata_mask[1*databits//8+i],
                self.dfi.phases[0].wrdata_mask[2*databits//8+i], self.dfi.phases[0].wrdata_mask[3*databits//8+i],
                self.dfi.phases[1].wrdata_mask[0*databits//8+i], self.dfi.phases[1].wrdata_mask[1*databits//8+i],
                self.dfi.phases[1].wrdata_mask[2*databits//8+i], self.dfi.phases[1].wrdata_mask[3*databits//8+i]),
            )
            self.sync += dm_data_d.eq(dm_data)
            self.comb += \
                If(bl8_sel,
                    dm_data_muxed.eq(dm_data_d[4:])
                ).Else(
                    dm_data_muxed.eq(dm_data[:4])
                )
            self.specials += \
                Instance("ODDRX2DQA",
                    i_D0=dm_data_muxed[0],
                    i_D1=dm_data_muxed[1],
                    i_D2=dm_data_muxed[2],
                    i_D3=dm_data_muxed[3],
                    i_RST=ResetSignal(),
                    i_DQSW270=dqsw270,
                    i_ECLK=ClockSignal("sys2x"),
                    i_SCLK=ClockSignal(),
                    o_Q=pads.dm[i]
                )

            dqs = Signal()
            dqs_oe_n = Signal()
            self.specials += \
                Instance("ODDRX2DQSB",
                    i_D0=dqs_serdes_pattern[0],
                    i_D1=dqs_serdes_pattern[1],
                    i_D2=dqs_serdes_pattern[2],
                    i_D3=dqs_serdes_pattern[3],
                    i_RST=ResetSignal(),
                    i_DQSW=dqsw,
                    i_ECLK=ClockSignal("sys2x"),
                    i_SCLK=ClockSignal(),
                    o_Q=dqs
                )
            self.specials += \
                Instance("TSHX2DQSA",
                    i_T0=~oe_dqs,
                    i_T1=~oe_dqs,
                    i_SCLK=ClockSignal(),
                    i_ECLK=ClockSignal("sys2x"),
                    i_DQSW=dqsw,
                    i_RST=ResetSignal(),
                    o_Q=dqs_oe_n,
                )
            self.specials += Tristate(pads.dqs_p[i], dqs, ~dqs_oe_n)

            for j in range(8*i, 8*(i+1)):
                dq_o = Signal()
                dq_i = Signal()
                dq_oe_n = Signal()
                dq_data = Signal(8)
                dq_data_d = Signal(8)
                dq_data_muxed = Signal(4)
                self.comb += dq_data.eq(Cat(
                    self.dfi.phases[0].wrdata[0*databits+j], self.dfi.phases[0].wrdata[1*databits+j],
                    self.dfi.phases[0].wrdata[2*databits+j], self.dfi.phases[0].wrdata[3*databits+j],
                    self.dfi.phases[1].wrdata[0*databits+j], self.dfi.phases[1].wrdata[1*databits+j],
                    self.dfi.phases[1].wrdata[2*databits+j], self.dfi.phases[1].wrdata[3*databits+j])
                )
                self.sync += dq_data_d.eq(dq_data)
                self.comb += \
                    If(bl8_sel,
                        dq_data_muxed.eq(dq_data_d[4:])
                    ).Else(
                        dq_data_muxed.eq(dq_data[:4])
                    )
                self.specials += \
                    Instance("ODDRX2DQA",
                        i_D0=dq_data_muxed[0],
                        i_D1=dq_data_muxed[1],
                        i_D2=dq_data_muxed[2],
                        i_D3=dq_data_muxed[3],
                        i_RST=ResetSignal(),
                        i_DQSW270=dqsw270,
                        i_ECLK=ClockSignal("sys2x"),
                        i_SCLK=ClockSignal(),
                        o_Q=dq_o
                    )
                dq_i_data = Signal(8)
                dq_i_data_d = Signal(8)
                self.specials += \
                    Instance("IDDRX2DQA",
                        i_D=pads.dq[j],
                        i_RST=ResetSignal(),
                        i_DQSR90=dqsr90,
                        i_SCLK=ClockSignal(),
                        i_ECLK=ClockSignal("sys2x"),
                        i_RDPNTR0=rdpntr[0], # CHECKME: are RDPNTR/WRPTNR
                        i_RDPNTR1=rdpntr[1], # behaving correctly when
                        i_RDPNTR2=rdpntr[2], # READ_0/READCLKSEL signals
                        i_WRPNTR0=wrpntr[0], # are not used. It should be
                        i_WRPNTR1=wrpntr[1], # be the case but needs to be
                        i_WRPNTR2=wrpntr[2], # verified.
                        o_Q0=dq_i_data[0],
                        o_Q1=dq_i_data[1],
                        o_Q2=dq_i_data[2],
                        o_Q3=dq_i_data[3],
                    )
                # debug
                if i == 0 and j == 0:
                    self.dq_i_data = dq_i_data
                dq_bitslip = BitSlip(4)
                self.comb += dq_bitslip.i.eq(dq_i_data)
                self.sync += \
                    If(self._dly_sel.storage[i],
                        If(self._rdly_dq_bitslip_rst.re,
                            dq_bitslip.value.eq(0)
                        ).Elif(self._rdly_dq_bitslip.re,
                            dq_bitslip.value.eq(dq_bitslip.value + 1)
                        )
                    )
                self.submodules += dq_bitslip
                self.sync += dq_i_data_d.eq(dq_i_data)
                self.comb += [
                    self.dfi.phases[0].rddata[i].eq(dq_bitslip.o[0]),
                    self.dfi.phases[0].rddata[databits+j].eq(dq_bitslip.o[1]),
                    self.dfi.phases[1].rddata[j].eq(dq_bitslip.o[2]),
                    self.dfi.phases[1].rddata[databits+j].eq(dq_bitslip.o[3])
                ]
                self.specials += \
                    Instance("TSHX2DQA",
                        i_T0=~oe_dq,
                        i_T1=~oe_dq,
                        i_SCLK=ClockSignal(),
                        i_ECLK=ClockSignal("sys2x"),
                        i_DQSW270=dqsw270,
                        i_RST=ResetSignal(),
                        o_Q=dq_oe_n,
                    )
                self.specials += Tristate(pads.dq[j], dq_o, ~dq_oe_n)

        # Flow control -----------------------------------------------------------------------------
        #
        # total read latency:
        #  N cycles through ODDRX2DQA FIXME
        #  cl_sys_latency cycles CAS
        #  M cycles through IDDRX2DQA FIXME
        rddata_en = self.dfi.phases[self.settings.rdphase].rddata_en
        for i in range(self.settings.read_latency-1):
            n_rddata_en = Signal()
            self.sync += n_rddata_en.eq(rddata_en)
            rddata_en = n_rddata_en
        self.sync += [phase.rddata_valid.eq(rddata_en | self._wlevel_en.storage)
            for phase in self.dfi.phases]

        oe = Signal()
        last_wrdata_en = Signal(cwl_sys_latency+3)
        wrphase = self.dfi.phases[self.settings.wrphase]
        self.sync += last_wrdata_en.eq(Cat(wrphase.wrdata_en, last_wrdata_en[:-1]))
        self.comb += oe.eq(
            last_wrdata_en[cwl_sys_latency-1] |
            last_wrdata_en[cwl_sys_latency] |
            last_wrdata_en[cwl_sys_latency+1] |
            last_wrdata_en[cwl_sys_latency+2])
        self.sync += \
            If(self._wlevel_en.storage,
                oe_dqs.eq(1), oe_dq.eq(0)
            ).Else(
                oe_dqs.eq(oe), oe_dq.eq(oe)
            )

        self.sync += bl8_sel.eq(last_wrdata_en[cwl_sys_latency-1])
