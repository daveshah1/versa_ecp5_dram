#ifndef __SDRAM_H
#define __SDRAM_H

#include <generated/csr.h>

void sdrsw(void);
void sdrhw(void);

#ifdef CSR_DDRPHY_WLEVEL_EN_ADDR
void sdrwlon(void);
void sdrwloff(void);
int write_level(void);
#endif

#ifdef CSR_DDRPHY_BASE
void sdrwlon(void);
void sdrwloff(void);
int sdrlevel(void);
#endif

int memtest(void);

int sdrinit(void);
void sdrdiag(void);

#endif /* __SDRAM_H */
