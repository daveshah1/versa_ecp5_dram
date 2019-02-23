#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <irq.h>
#include <uart.h>
#include <console.h>
#include <generated/csr.h>

#include "sdram.h"

static char *readstr(void)
{
	char c[2];
	static char s[64];
	static int ptr = 0;

	if(readchar_nonblock()) {
		c[0] = readchar();
		c[1] = 0;
		switch(c[0]) {
			case 0x7f:
			case 0x08:
				if(ptr > 0) {
					ptr--;
					putsnonl("\x08 \x08");
				}
				break;
			case 0x07:
				break;
			case '\r':
			case '\n':
				s[ptr] = 0x00;
				putsnonl("\n");
				ptr = 0;
				return s;
			default:
				if(ptr >= (sizeof(s) - 1))
					break;
				putsnonl(c);
				s[ptr] = c[0];
				ptr++;
				break;
		}
	}

	return NULL;
}

static char *get_token(char **str)
{
	char *c, *d;

	c = (char *)strchr(*str, ' ');
	if(c == NULL) {
		d = *str;
		*str = *str+strlen(*str);
		return d;
	}
	*c = 0;
	d = *str;
	*str = c+1;
	return d;
}

static void prompt(void)
{
	printf("RUNTIME>");
}

static void help(void)
{
	puts("Available commands:");
	puts("help                            - this command");
	puts("reboot                          - reboot CPU");
	puts("");
	puts("sdram_init                      - initialize SDRAM");
	puts("sdram_test                      - test SDRAM from CPU");
	puts("");
	puts("debug:");
	puts("phy_diag                        - diagnose SDRAM");
	puts("phy_reset                       - reset SDRAM PHY");
	puts("phy_bitslip                     - read bitslip");
	puts("");
}

static void reboot(void)
{
	ctrl_reset_write(1);
}

static void sdram_init(void)
{
	sdrinit();
}

static void sdram_test(void)
{
	memtest();
}

static void phy_reset(void)
{
	int i;
	printf("Reseting SDRAM PHY.\n");
	for(i=0; i<NBMODULES; i++) {
		ddrphy_dly_sel_write(1<<i);
		ddrphy_rdly_dq_rst_write(1);
		ddrphy_rdly_dq_bitslip_rst_write(1);
#ifdef CSR_DDRPHY_WLEVEL_EN_ADDR
		ddrphy_wdly_dq_rst_write(1);
		ddrphy_wdly_dqs_rst_write(1);
#endif
	}
}

static void phy_bitslip(void)
{
	printf("Read bitslip.\n");
	ddrphy_dly_sel_write(1<<0);
	ddrphy_rdly_dq_bitslip_write(1);
	ddrphy_dly_sel_write(1<<1);
	ddrphy_rdly_dq_bitslip_write(1);
}

static void console_service(void)
{
	char *str;
	char *token;

	str = readstr();
	if(str == NULL) return;
	token = get_token(&str);
	if(strcmp(token, "help") == 0)
		help();
	else if(strcmp(token, "reboot") == 0)
		reboot();
	else if(strcmp(token, "sdram_test") == 0)
		sdram_test();
	else if(strcmp(token, "sdram_init") == 0)
		sdram_init();
	else if(strcmp(token, "phy_diag") == 0)
		sdrdiag();
	else if(strcmp(token, "phy_reset") == 0)
		phy_reset();
	else if(strcmp(token, "phy_bitslip") == 0)
		phy_bitslip();
	prompt();
}

int main(void)
{
	irq_setmask(0);
	irq_setie(1);

	uart_init();
	puts("\nLiteDRAM CPU testing software built "__DATE__" "__TIME__"\n");
	help();
	prompt();

	while(1) {
		console_service();
	}

	return 0;
}
