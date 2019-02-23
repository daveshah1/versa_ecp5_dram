#include <generated/csr.h>

#include <stdio.h>
#include <stdlib.h>

#ifdef CSR_SDRAM_BASE
#include <generated/sdram_phy.h>
#endif
#include <generated/mem.h>
#include <hw/flags.h>
#include <system.h>

#include "sdram.h"

static void cdelay(int i)
{
	while(i > 0) {
#if defined (__lm32__)
		__asm__ volatile("nop");
#elif defined (__or1k__)
		__asm__ volatile("l.nop");
#elif defined (__picorv32__)
		__asm__ volatile("nop");
#elif defined (__vexriscv__)
		__asm__ volatile("nop");
#elif defined (__minerva__)
		__asm__ volatile("nop");
#else
#error Unsupported architecture
#endif
		i--;
	}
}

#ifdef CSR_SDRAM_BASE

void sdrsw(void)
{
	sdram_dfii_control_write(DFII_CONTROL_CKE|DFII_CONTROL_ODT|DFII_CONTROL_RESET_N);
	printf("SDRAM now under software control\n");
}

void sdrhw(void)
{
	sdram_dfii_control_write(DFII_CONTROL_SEL);
	printf("SDRAM now under hardware control\n");
}

#ifdef CSR_DDRPHY_BASE

#ifdef USDDRPHY
#define ERR_DDRPHY_DELAY 512
#else
#define ERR_DDRPHY_DELAY 32
#endif
#define ERR_DDRPHY_BITSLIP 8

#define NBMODULES DFII_PIX_DATA_SIZE/2

#ifdef CSR_DDRPHY_WLEVEL_EN_ADDR

void sdrwlon(void)
{
	sdram_dfii_pi0_address_write(DDRX_MR1 | (1 << 7));
	sdram_dfii_pi0_baddress_write(1);
	command_p0(DFII_COMMAND_RAS|DFII_COMMAND_CAS|DFII_COMMAND_WE|DFII_COMMAND_CS);
	ddrphy_wlevel_en_write(1);
}

void sdrwloff(void)
{
	sdram_dfii_pi0_address_write(DDRX_MR1);
	sdram_dfii_pi0_baddress_write(1);
	command_p0(DFII_COMMAND_RAS|DFII_COMMAND_CAS|DFII_COMMAND_WE|DFII_COMMAND_CS);
	ddrphy_wlevel_en_write(0);
}

static void write_delay_reset(int module) {
	int i;

	/* sel module */
	ddrphy_dly_sel_write(1 << module);

	/* reset delay */
	ddrphy_wdly_dq_rst_write(1);
	ddrphy_wdly_dqs_rst_write(1);
#ifdef USDDRPHY /* need to init manually on Ultrascale */
	for(i=0; i<ddrphy_half_sys8x_taps_read(); i++)
		ddrphy_wdly_dqs_inc_write(1);
#endif
	ddrphy_dly_sel_write(0);
}

static void write_delay_inc(int module) {
	/* sel module */
	ddrphy_dly_sel_write(1 << module);

	/* inc delay */
	ddrphy_wdly_dq_inc_write(1);
	ddrphy_wdly_dqs_inc_write(1);

	ddrphy_dly_sel_write(0);
}

int write_level(void)
{
	int i, j, k;

	int dq_address;
	unsigned char dq;

	int err_ddrphy_wdly;

	unsigned char taps_scan[ERR_DDRPHY_DELAY];

	int one_window_active;
	int one_window_start, one_window_best_start;
	int one_window_count, one_window_best_count;

	int delays[NBMODULES];

	int ok;

	err_ddrphy_wdly = ERR_DDRPHY_DELAY - ddrphy_half_sys8x_taps_read();

	printf("Write leveling:\n");

	sdrwlon();
	cdelay(100);
	for(i=0;i<NBMODULES;i++) {
		printf("m%d: |", i);
		dq_address = sdram_dfii_pix_rddata_addr[0]+4*(NBMODULES-1-i);

		/* reset delay */
		write_delay_reset(i);

		/* scan write delay taps */
		for(j=0;j<err_ddrphy_wdly;j++) {
			int zero_count = 0;
			int one_count = 0;
			int show = 1;
#ifdef USDDRPHY
			show = (j%16 == 0);
#endif
			for (k=0; k<128; k++) {
				ddrphy_wlevel_strobe_write(1);
				cdelay(10);
				dq = MMPTR(dq_address);
				if (dq != 0)
					one_count++;
				else
					zero_count++;
			}
			if (one_count > zero_count)
				taps_scan[j] = 1;
			else
				taps_scan[j] = 0;
			if (show)
				printf("%d", taps_scan[j]);
			write_delay_inc(i);
			cdelay(10);
		}
		printf("|");

		/* find longer 1 window and set delay at the 0/1 transition */
		one_window_active = 0;
		one_window_start = 0;
		one_window_count = 0;
		one_window_best_start = 0;
		one_window_best_count = 0;
		delays[i] = -1;
		for(j=0;j<err_ddrphy_wdly;j++) {
			if (one_window_active) {
				if ((taps_scan[j] == 0) | (j == err_ddrphy_wdly - 1)) {
					one_window_active = 0;
					one_window_count = j - one_window_start;
					if (one_window_count > one_window_best_count) {
						one_window_best_start = one_window_start;
						one_window_best_count = one_window_count;
					}
				}
			} else {
				if (taps_scan[j]) {
					one_window_active = 1;
					one_window_start = j;
				}
			}
		}
		if (sdrwl_delays[i] >= 0)
			delays[i] = sdrwl_delays[i];
		else
			delays[i] = one_window_best_start;

		/* configure write delay */
		write_delay_reset(i);
		for(j=0; j<delays[i]; j++)
			write_delay_inc(i);
		printf(" delay%c: %02d\n", (sdrwl_delays[i] >= 0) ? '*' : ' ', delays[i]);
	}

	sdrwloff();

	ok = 1;
	for(i=NBMODULES-1;i>=0;i--) {
		if(delays[i] < 0)
			ok = 0;
	}

	return ok;
}

#endif /* CSR_DDRPHY_WLEVEL_EN_ADDR */

static void read_bitslip_inc(char m)
{
		ddrphy_dly_sel_write(1 << m);
		ddrphy_rdly_dq_bitslip_write(1);
		ddrphy_dly_sel_write(0);
}

static int read_level_scan(int module, int bitslip)
{
	unsigned int prv;
	unsigned char prs[DFII_NPHASES*DFII_PIX_DATA_SIZE];
	int p, i, j;
	int score;

	/* Generate pseudo-random sequence */
	prv = 42;
	for(i=0;i<DFII_NPHASES*DFII_PIX_DATA_SIZE;i++) {
		prv = 1664525*prv + 1013904223;
		prs[i] = prv;
	}

	/* Activate */
	sdram_dfii_pi0_address_write(0);
	sdram_dfii_pi0_baddress_write(0);
	command_p0(DFII_COMMAND_RAS|DFII_COMMAND_CS);
	cdelay(15);

	/* Write test pattern */
	for(p=0;p<DFII_NPHASES;p++)
		for(i=0;i<DFII_PIX_DATA_SIZE;i++)
			MMPTR(sdram_dfii_pix_wrdata_addr[p]+4*i) = prs[DFII_PIX_DATA_SIZE*p+i];
	sdram_dfii_piwr_address_write(0);
	sdram_dfii_piwr_baddress_write(0);
	command_pwr(DFII_COMMAND_CAS|DFII_COMMAND_WE|DFII_COMMAND_CS|DFII_COMMAND_WRDATA);

	/* Calibrate each DQ in turn */
	sdram_dfii_pird_address_write(0);
	sdram_dfii_pird_baddress_write(0);
	score = 0;

	printf("m%d, b%d: |", module, bitslip);
	ddrphy_dly_sel_write(1 << module);
	ddrphy_rdly_dq_rst_write(1);
	ddrphy_dly_sel_write(0);
	for(j=0; j<ERR_DDRPHY_DELAY;j++) {
		int working;
		int show = 1;
#ifdef USDDRPHY
		show = (j%16 == 0);
#endif
		command_prd(DFII_COMMAND_CAS|DFII_COMMAND_CS|DFII_COMMAND_RDDATA);
		cdelay(15);
		working = 1;
		for(p=0;p<DFII_NPHASES;p++) {
			if(MMPTR(sdram_dfii_pix_rddata_addr[p]+4*(NBMODULES-module-1)) != prs[DFII_PIX_DATA_SIZE*p+(NBMODULES-module-1)])
				working = 0;
			if(MMPTR(sdram_dfii_pix_rddata_addr[p]+4*(2*NBMODULES-module-1)) != prs[DFII_PIX_DATA_SIZE*p+2*NBMODULES-module-1])
				working = 0;
		}
		if (show)
			printf("%d", working);
		score += working;
		ddrphy_rdly_dq_inc_write(1);
		ddrphy_dly_sel_write(0);
	}
	printf("| ");

	/* Precharge */
	sdram_dfii_pi0_address_write(0);
	sdram_dfii_pi0_baddress_write(0);
	command_p0(DFII_COMMAND_RAS|DFII_COMMAND_WE|DFII_COMMAND_CS);
	cdelay(15);

	return score;
}

static void read_level(int module)
{
	unsigned int prv;
	unsigned char prs[DFII_NPHASES*DFII_PIX_DATA_SIZE];
	int p, i, j;
	int working;
	int delay, delay_min, delay_max;

	printf("delays: ");

	/* Generate pseudo-random sequence */
	prv = 42;
	for(i=0;i<DFII_NPHASES*DFII_PIX_DATA_SIZE;i++) {
		prv = 1664525*prv + 1013904223;
		prs[i] = prv;
	}

	/* Activate */
	sdram_dfii_pi0_address_write(0);
	sdram_dfii_pi0_baddress_write(0);
	command_p0(DFII_COMMAND_RAS|DFII_COMMAND_CS);
	cdelay(15);

	/* Write test pattern */
	for(p=0;p<DFII_NPHASES;p++)
		for(i=0;i<DFII_PIX_DATA_SIZE;i++)
			MMPTR(sdram_dfii_pix_wrdata_addr[p]+4*i) = prs[DFII_PIX_DATA_SIZE*p+i];
	sdram_dfii_piwr_address_write(0);
	sdram_dfii_piwr_baddress_write(0);
	command_pwr(DFII_COMMAND_CAS|DFII_COMMAND_WE|DFII_COMMAND_CS|DFII_COMMAND_WRDATA);

	/* Calibrate each DQ in turn */
	sdram_dfii_pird_address_write(0);
	sdram_dfii_pird_baddress_write(0);

	ddrphy_dly_sel_write(1 << module);
	delay = 0;
	ddrphy_dly_sel_write(0);
	/* Find smallest working delay */
	ddrphy_rdly_dq_rst_write(1);
	while(1) {
		command_prd(DFII_COMMAND_CAS|DFII_COMMAND_CS|DFII_COMMAND_RDDATA);
		cdelay(15);
		working = 1;
		for(p=0;p<DFII_NPHASES;p++) {
			if(MMPTR(sdram_dfii_pix_rddata_addr[p]+4*(NBMODULES-module-1)) != prs[DFII_PIX_DATA_SIZE*p+(NBMODULES-module-1)])
				working = 0;
			if(MMPTR(sdram_dfii_pix_rddata_addr[p]+4*(2*NBMODULES-module-1)) != prs[DFII_PIX_DATA_SIZE*p+2*NBMODULES-module-1])
				working = 0;
		}
		if(working)
			break;
		delay++;
		if(delay >= ERR_DDRPHY_DELAY)
			break;
		ddrphy_dly_sel_write(1 << module);
		ddrphy_rdly_dq_inc_write(1);
		ddrphy_dly_sel_write(0);
	}
	delay_min = delay;

	/* Get a bit further into the working zone */
#ifdef USDDRPHY
	for(j=0;j<16;j++) {
		delay += 1;
		ddrphy_rdly_dq_inc_write(1);
	}
#else
	delay++;
	ddrphy_rdly_dq_inc_write(1);
#endif

	/* Find largest working delay */
	while(1) {
		command_prd(DFII_COMMAND_CAS|DFII_COMMAND_CS|DFII_COMMAND_RDDATA);
		cdelay(15);
		working = 1;
		for(p=0;p<DFII_NPHASES;p++) {
			if(MMPTR(sdram_dfii_pix_rddata_addr[p]+4*(NBMODULES-module-1)) != prs[DFII_PIX_DATA_SIZE*p+(NBMODULES-module-1)])
				working = 0;
			if(MMPTR(sdram_dfii_pix_rddata_addr[p]+4*(2*NBMODULES-module-1)) != prs[DFII_PIX_DATA_SIZE*p+2*NBMODULES-module-1])
				working = 0;
		}
		if(!working)
			break;
		delay++;
		if(delay >= ERR_DDRPHY_DELAY)
			break;
		ddrphy_rdly_dq_inc_write(1);
	}
	delay_max = delay;

	if (delay_min >= ERR_DDRPHY_DELAY)
		printf("-");
	else
		printf("%02d+-%02d", (delay_min+delay_max)/2, (delay_max-delay_min)/2);

	/* Set delay to the middle */
	ddrphy_rdly_dq_rst_write(1);
	for(j=0;j<(delay_min+delay_max)/2;j++)
		ddrphy_rdly_dq_inc_write(1);

	/* Precharge */
	sdram_dfii_pi0_address_write(0);
	sdram_dfii_pi0_baddress_write(0);
	command_p0(DFII_COMMAND_RAS|DFII_COMMAND_WE|DFII_COMMAND_CS);
	cdelay(15);
}
#endif /* CSR_DDRPHY_BASE */

#endif /* CSR_SDRAM_BASE */

static unsigned int seed_to_data_32(unsigned int seed, int random)
{
	if (random)
		return 1664525*seed + 1013904223;
	else
		return seed + 1;
}

static unsigned short seed_to_data_16(unsigned short seed, int random)
{
	if (random)
		return 25173*seed + 13849;
	else
		return seed + 1;
}

#define ONEZERO 0xAAAAAAAA
#define ZEROONE 0x55555555

#ifndef MEMTEST_BUS_SIZE
#define MEMTEST_BUS_SIZE (512)
#endif

//#define MEMTEST_BUS_DEBUG

static int memtest_bus(void)
{
	volatile unsigned int *array = (unsigned int *)MAIN_RAM_BASE;
	int i, errors;
	unsigned int rdata;

	errors = 0;

	for(i=0;i<MEMTEST_BUS_SIZE/4;i++) {
		array[i] = ONEZERO;
	}
	flush_cpu_dcache();
#ifdef L2_SIZE
	flush_l2_cache();
#endif
	for(i=0;i<MEMTEST_BUS_SIZE/4;i++) {
		rdata = array[i];
		if(rdata != ONEZERO) {
			errors++;
#ifdef MEMTEST_BUS_DEBUG
			printf("[bus: 0x%0x]: 0x%08x vs 0x%08x\n", i, rdata, ONEZERO);
#endif
		}
	}

	for(i=0;i<MEMTEST_BUS_SIZE/4;i++) {
		array[i] = ZEROONE;
	}
	flush_cpu_dcache();
#ifdef L2_SIZE
	flush_l2_cache();
#endif
	for(i=0;i<MEMTEST_BUS_SIZE/4;i++) {
		rdata = array[i];
		if(rdata != ZEROONE) {
			errors++;
#ifdef MEMTEST_BUS_DEBUG
			printf("[bus 0x%0x]: 0x%08x vs 0x%08x\n", i, rdata, ZEROONE);
#endif
		}
	}

	return errors;
}

#ifndef MEMTEST_DATA_SIZE
#define MEMTEST_DATA_SIZE (2*1024*1024)
#endif
#define MEMTEST_DATA_RANDOM 1

//#define MEMTEST_DATA_DEBUG

static int memtest_data(void)
{
	volatile unsigned int *array = (unsigned int *)MAIN_RAM_BASE;
	int i, errors;
	unsigned int seed_32;
	unsigned int rdata;

	errors = 0;
	seed_32 = 0;

	for(i=0;i<MEMTEST_DATA_SIZE/4;i++) {
		seed_32 = seed_to_data_32(seed_32, MEMTEST_DATA_RANDOM);
		array[i] = seed_32;
	}

	seed_32 = 0;
	flush_cpu_dcache();
#ifdef L2_SIZE
	flush_l2_cache();
#endif
	for(i=0;i<MEMTEST_DATA_SIZE/4;i++) {
		seed_32 = seed_to_data_32(seed_32, MEMTEST_DATA_RANDOM);
		rdata = array[i];
		if(rdata != seed_32) {
			errors++;
#ifdef MEMTEST_DATA_DEBUG
			printf("[data 0x%0x]: 0x%08x vs 0x%08x\n", i, rdata, seed_32);
#endif
		}
	}

	return errors;
}
#ifndef MEMTEST_ADDR_SIZE
#define MEMTEST_ADDR_SIZE (32*1024)
#endif
#define MEMTEST_ADDR_RANDOM 0

//#define MEMTEST_ADDR_DEBUG

static int memtest_addr(void)
{
	volatile unsigned int *array = (unsigned int *)MAIN_RAM_BASE;
	int i, errors;
	unsigned short seed_16;
	unsigned short rdata;

	errors = 0;
	seed_16 = 0;

	for(i=0;i<MEMTEST_ADDR_SIZE/4;i++) {
		seed_16 = seed_to_data_16(seed_16, MEMTEST_ADDR_RANDOM);
		array[(unsigned int) seed_16] = i;
	}

	seed_16 = 0;
	flush_cpu_dcache();
#ifdef L2_SIZE
	flush_l2_cache();
#endif
	for(i=0;i<MEMTEST_ADDR_SIZE/4;i++) {
		seed_16 = seed_to_data_16(seed_16, MEMTEST_ADDR_RANDOM);
		rdata = array[(unsigned int) seed_16];
		if(rdata != i) {
			errors++;
#ifdef MEMTEST_ADDR_DEBUG
			printf("[addr 0x%0x]: 0x%08x vs 0x%08x\n", i, rdata, i);
#endif
		}
	}

	return errors;
}

int memtest(void)
{
	int bus_errors, data_errors, addr_errors;

	bus_errors = memtest_bus();
	if(bus_errors != 0)
		printf("Memtest bus failed: %d/%d errors\n", bus_errors, 2*128);

	data_errors = memtest_data();
	if(data_errors != 0)
		printf("Memtest data failed: %d/%d errors\n", data_errors, MEMTEST_DATA_SIZE/4);

	addr_errors = memtest_addr();
	if(addr_errors != 0)
		printf("Memtest addr failed: %d/%d errors\n", addr_errors, MEMTEST_ADDR_SIZE/4);

	if(bus_errors + data_errors + addr_errors != 0)
		return 0;
	else {
		printf("Memtest OK\n");
		return 1;
	}
}

#ifdef CSR_SDRAM_BASE

#ifdef CSR_DDRPHY_BASE
int sdrlevel(void)
{
	int i, j;

	sdrsw();

	for(i=0; i<NBMODULES; i++) {
		ddrphy_dly_sel_write(1<<i);
		ddrphy_rdly_dq_rst_write(1);
		ddrphy_rdly_dq_bitslip_rst_write(1);
		ddrphy_dly_sel_write(0);
	}

	printf("Read leveling:\n");

	/* reset delay */
	ddrphy_dly_sel_write(1);
	ddrphy_rdly_dq_rst_write(1);
	ddrphy_dly_sel_write(2);
	ddrphy_rdly_dq_rst_write(1);
	ddrphy_dly_sel_write(0);
	/* Activate */
	sdram_dfii_pi0_address_write(0);
	sdram_dfii_pi0_baddress_write(0);
	command_p0(DFII_COMMAND_RAS|DFII_COMMAND_CS);
	cdelay(15);

	/* Find Read delay */
	for(i=0; i<128; i++) {
		unsigned int burstdet_count;
		printf("delay %d | ", i);
		burstdet_count = 0;
		for (j=0; j<2; j++) {
			ddrphy_burstdet_rst_write(1);
			command_prd(DFII_COMMAND_CAS|DFII_COMMAND_CS|DFII_COMMAND_RDDATA);
			cdelay(100);
			burstdet_count += (ddrphy_burstdet_found_read() != 0);
		}
		printf(" burstdet_count : %d\n", burstdet_count);
		if (burstdet_count == 2)
			break;

		/* Inc delay */
		ddrphy_dly_sel_write(1);
		ddrphy_rdly_dq_inc_write(1);
		ddrphy_dly_sel_write(2);
		ddrphy_rdly_dq_inc_write(1);
		ddrphy_dly_sel_write(0);
	}

	/* Precharge */
	sdram_dfii_pi0_address_write(0);
	sdram_dfii_pi0_baddress_write(0);
	command_p0(DFII_COMMAND_RAS|DFII_COMMAND_WE|DFII_COMMAND_CS);
	cdelay(15);

	return 1;
}
#endif

int sdrinit(void)
{
	printf("Initializing SDRAM...\n");

	init_sequence();
#ifdef CSR_DDRPHY_BASE
#if CSR_DDRPHY_EN_VTC_ADDR
	ddrphy_en_vtc_write(0);
#endif
	sdrlevel();
#if CSR_DDRPHY_EN_VTC_ADDR
	ddrphy_en_vtc_write(1);
#endif
#endif
	sdrhw();
	if(!memtest()) {
		return 0;
	}

	return 1;
}

#define MPR0_SEL (0 << 0)
#define MPR1_SEL (1 << 0)
#define MPR2_SEL (2 << 0)
#define MPR3_SEL (3 << 0)

#define MPR_ENABLE (1 << 2)

#define MPR_READ_SERIAL    (0 << 11)
#define MPR_READ_PARALLEL  (1 << 11)
#define MPR_READ_STAGGERED (2 << 11)

static void sdrmpron(char mpr)
{
	sdram_dfii_pi0_address_write(MPR_READ_SERIAL | MPR_ENABLE | mpr);
	sdram_dfii_pi0_baddress_write(3);
	command_p0(DFII_COMMAND_RAS|DFII_COMMAND_CAS|DFII_COMMAND_WE|DFII_COMMAND_CS);
}

static void sdrmproff(void)
{
	sdram_dfii_pi0_address_write(0);
	sdram_dfii_pi0_baddress_write(3);
	command_p0(DFII_COMMAND_RAS|DFII_COMMAND_CAS|DFII_COMMAND_WE|DFII_COMMAND_CS);
}

void sdrdiag(void)
{
	int module, phase;
	printf("Diagnose SDRAM...\n");

	/* reset phy */
	for(module=0; module<NBMODULES; module++) {
		ddrphy_dly_sel_write(1<<module);
		ddrphy_rdly_dq_rst_write(1);
		ddrphy_rdly_dq_bitslip_rst_write(1);
		ddrphy_dly_sel_write(0);
	}

	/* software control */
	sdrsw();

	printf("Reads with MPR0 (0b01010101) enabled...\n");
	sdrmpron(MPR0_SEL);
	command_prd(DFII_COMMAND_CAS|DFII_COMMAND_CS|DFII_COMMAND_RDDATA);
	cdelay(15);
	for (module=0; module < NBMODULES; module++) {
		printf("m%d: ", module);
		for(phase=0; phase<DFII_NPHASES; phase++) {
			printf("%d", MMPTR(sdram_dfii_pix_rddata_addr[phase]+4*(NBMODULES-module-1)) & 0x1);
			printf("%d", MMPTR(sdram_dfii_pix_rddata_addr[phase]+4*(2*NBMODULES-module-1)) & 0x1);
		}
		printf("\n");
	}
	sdrmproff();

	/* hardware control */
	sdrhw();
}

#endif
