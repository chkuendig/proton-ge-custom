# Linux ROCm backend and a matching PE64/PE32/Unix64 Wine bridge.
ifeq ($(TARGET_ARCH),x86_64)

ZLUDA_BUILD := $(OBJ)/obj-zluda
ZLUDA_DEPS := $(OBJ)/zluda-deps
ZLUDA_RUST := $(ZLUDA_DEPS)/rust
ZLUDA_ROCM := $(ZLUDA_DEPS)/sdk/opt/rocm-7.2.4
ZLUDA_WINE_BUILD := $(OBJ)/obj-zluda-wine
ZLUDA_WINE_SDK := $(OBJ)/zluda-wine-sdk
ZLUDA_SOURCE_ARGS := --exclude=/target --exclude=/ext/llvm-project --exclude=/ext/HiGHS

$(eval $(call rules-source,zluda,$(SRCDIR)/zluda))
$(eval $(call rules-source,zluda-wine,$(SRCDIR)/zluda-wine))

$(OBJ)/.zluda-post-source:
	ln -sfn "$(ZLUDA_ORIGIN)/ext/llvm-project" "$(ZLUDA_SRC)/ext/llvm-project"
	ln -sfn "$(ZLUDA_ORIGIN)/ext/HiGHS" "$(ZLUDA_SRC)/ext/HiGHS"
	ln -sfn "$$(git -C "$(ZLUDA_ORIGIN)" rev-parse --absolute-git-dir)" "$(ZLUDA_SRC)/.git"
	touch $@

$(OBJ)/.zluda-deps: $(SRC)/make/zluda-deps.json $(SRC)/make/zluda.py $(SRC)/make/zluda-build.sh
	python3 "$(SRC)/make/zluda.py" fetch "$(ZLUDA_DEPS)"
	bash "$(SRC)/make/zluda-build.sh" deps "$(ZLUDA_DEPS)"
	touch $@

$(OBJ)/.zluda-build: $(OBJ)/.zluda-post-source $(OBJ)/.zluda-deps
$(OBJ)/.zluda-build: $(SRC)/make/zluda-math-compat.c $(SRC)/make/zluda-cmake.py
# Fixed reviewed source epochs keep component build metadata reproducible.
	env SOURCE_DATE_EPOCH=1793802357 ZLUDA_HIP_SDK_ROOT="$(ZLUDA_ROCM)" SCCACHE_PATH="$$(command -v ccache)" \
	    bash "$(SRC)/make/zluda-build.sh" native "$(ZLUDA_DEPS)" "$(ZLUDA_SRC)" "$(ZLUDA_BUILD)"
	touch $@

$(OBJ)/.zluda-dist: $(OBJ)/.zluda-build
	python3 "$(SRC)/make/zluda.py" native "$(ZLUDA_DEPS)" "$(ZLUDA_BUILD)" "$(DST_BASE)" "$(ZLUDA_ORIGIN)"
	touch $@

$(OBJ)/.zluda-wine-build: $(OBJ)/.zluda-wine-post-source $(OBJ)/.zluda-deps $(OBJ)/.wine-x86_64-build
$(OBJ)/.zluda-wine-build: $(SRC)/make/zluda-wine-sdk.py $(SRC)/make/zluda-wine-abi.json
	python3 "$(SRC)/make/zluda-wine-sdk.py" "$(WINE_SRC)" "$(WINE_x86_64_OBJ)" "$(ZLUDA_WINE_SDK)"
	rm -rf "$(ZLUDA_WINE_BUILD)"
	env WINE_SDK_ROOT="$(ZLUDA_WINE_SDK)" ZLUDA_WINE_BACKEND_SOURCE="$(ZLUDA_ORIGIN)" \
	    ZLUDA_WINE_SOURCE_ID="$$(git -C "$(ZLUDA_WINE_ORIGIN)" rev-parse HEAD)" \
	    ZLUDA_WINE_ABI_RECEIPT="$(ZLUDA_WINE_SDK)/ABI-RECEIPT.json" \
	    ZLUDA_WINE_ABI_RECEIPT_SHA256="$$(sha256sum "$(ZLUDA_WINE_SDK)/ABI-RECEIPT.json" | cut -d' ' -f1)" \
	    ZLUDA_WINE_BUILD_ROOT="$(ZLUDA_WINE_BUILD)" SOURCE_DATE_EPOCH=1793751497 \
	    PATH="$(ZLUDA_WINE_SDK)/bin:$(ZLUDA_RUST)/bin:$(PATH)" JOBS=3 \
	    bash "$(ZLUDA_WINE_SRC)/wine/zluda_wine/build-bridge.sh"
	touch $@

# Install only after Wine has copied both architectures into the distribution.
$(OBJ)/.zluda-wine-dist: $(OBJ)/.zluda-wine-build $(OBJ)/.wine-x86_64-dist $(OBJ)/.wine-i386-dist
$(OBJ)/.zluda-wine-dist: $(SRC)/make/zluda.py $(SRC)/proton
	python3 "$(SRC)/make/zluda.py" bridge "$(ZLUDA_WINE_BUILD)" "$(DST_BASE)" "$(ZLUDA_WINE_ORIGIN)" "$(WINE_SRC)" "$(OBJ)"
	touch $@

zluda: $(OBJ)/.zluda-dist
zluda-wine: $(OBJ)/.zluda-wine-dist
zluda-deps: $(OBJ)/.zluda-deps
all all-dist default_pfx: zluda zluda-wine
.PHONY: zluda zluda-wine zluda-deps zluda-smoke zluda-launcher-smoke

zluda-smoke:
	PYTHONDONTWRITEBYTECODE=1 python3 "$(SRC)/tests/zluda/smoke.py" "$(abspath $(REDIST_DIR))" "$(OBJ)"

zluda-launcher-smoke:
	PYTHONDONTWRITEBYTECODE=1 python3 "$(SRC)/tests/zluda/launcher.py" "$(abspath $(REDIST_DIR))" "$(OBJ)"

zluda-clean::
	rm -rf "$(ZLUDA_BUILD)" "$(OBJ)/.zluda-build" "$(OBJ)/.zluda-dist"
zluda-wine-clean::
	rm -rf "$(ZLUDA_WINE_BUILD)" "$(ZLUDA_WINE_SDK)" "$(OBJ)/.zluda-wine-build" "$(OBJ)/.zluda-wine-dist"
endif
