################################################################################
#
# sysbench
#
################################################################################

SYSBENCH_VERSION = 1.0.20
SYSBENCH_SITE = $(call github,akopytov,sysbench,$(SYSBENCH_VERSION))
SYSBENCH_LICENSE = GPL-2.0+
SYSBENCH_LICENSE_FILES = COPYING

SYSBENCH_AUTORECONF = YES

SYSBENCH_DEPENDENCIES = host-pkgconf ck luajit

# Force detection of some functions and link with pthread and libm
SYSBENCH_CONF_ENV = \
	ac_cv_func_pthread_once=yes \
	ac_cv_func_isatty=yes \
	ac_cv_func_posix_memalign=yes \
	ac_cv_func_aligned_alloc=yes \
	LIBS="-lpthread -lm"

SYSBENCH_CONF_OPTS = \
	--without-lib-prefix \
	--with-system-luajit \
	--with-system-ck \
	--without-mysql \
	--without-pgsql

$(eval $(autotools-package))
