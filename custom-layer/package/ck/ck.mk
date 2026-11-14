################################################################################
#
# CK (Concurrency Kit)
#
################################################################################

CK_VERSION = 0.7.2
CK_SITE = $(call github,concurrencykit,ck,$(CK_VERSION))
CK_LICENSE = BSD-2-Clause
CK_LICENSE_FILES = LICENSE
CK_INSTALL_STAGING = YES

define CK_CONFIGURE_CMDS
	(cd $(@D); \
		$(TARGET_CONFIGURE_OPTS) \
		./configure \
		--prefix=/usr \
		--platform=$(BR2_ARCH) \
	)
endef

define CK_BUILD_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D)
endef

define CK_INSTALL_STAGING_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D) DESTDIR=$(STAGING_DIR) install
endef

define CK_INSTALL_TARGET_CMDS
	$(TARGET_MAKE_ENV) $(MAKE) -C $(@D) DESTDIR=$(TARGET_DIR) install
endef

define CK_FIX_CONFIGURE_FOR_CROSS
	$(SED) 's/CC -o \.1 \.1\.c/CC -static -o .1 .1.c/' $(@D)/configure
endef
CK_PRE_CONFIGURE_HOOKS += CK_FIX_CONFIGURE_FOR_CROSS

$(eval $(generic-package))
