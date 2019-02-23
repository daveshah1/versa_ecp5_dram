#ifndef __SDRAM_H
#define __SDRAM_H

#include <generated/csr.h>

#define NBMODULES CSR_SDRAM_DFII_PI0_WRDATA_SIZE/2

void sdrsw(void);
void sdrhw(void);

#ifdef CSR_DDRPHY_WLEVEL_EN_ADDR
void sdrwlon(void);
void sdrwloff(void);
int write_level(void);
#endif

#ifdef CSR_DDRPHY_BASE

int sdrwl_delays[16];

void sdrwlon(void);
void sdrwloff(void);
int sdrlevel(void);
#endif

int memtest(void);

int sdrinit(void);
void sdrdiag(void);

#endif /* __SDRAM_H */
