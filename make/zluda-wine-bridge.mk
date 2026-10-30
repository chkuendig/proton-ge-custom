# CUDA builtin PE modules + Unixlib are owned by ZLUDA. The backend/runtime
# overlay remains separate; GE owns its stock SveSop nvidia-libs relay.
ifeq ($(TARGET_ARCH),x86_64)

$(eval $(call rules-source,zluda-bridge,$(SRCDIR)/zluda/wine/zluda_wine))

ZLUDA_BRIDGE_BUILD := $(OBJ)/obj-zluda-wine-bridge
ZLUDA_BRIDGE_SDK := $(ZLUDA_BRIDGE_BUILD)/wine-sdk
ZLUDA_BRIDGE_PAYLOAD := $(ZLUDA_BRIDGE_BUILD)/payload
ZLUDA_BRIDGE_SCRIPTS := $(SRC)/packaging/zluda-overlay

$(OBJ)/.zluda-bridge-build: $(OBJ)/.zluda-bridge-post-source $(OBJ)/.wine-x86_64-build
$(OBJ)/.zluda-bridge-build: $(ZLUDA_BRIDGE_SCRIPTS)/build-bridge.sh $(ZLUDA_BRIDGE_SCRIPTS)/wine_sdk.py $(ZLUDA_BRIDGE_SCRIPTS)/cuda_package.py
$(OBJ)/.zluda-bridge-build: $(ZLUDA_BRIDGE_SCRIPTS)/wine_provider.py

$(OBJ)/.zluda-bridge-build:
	rm -rf "$(ZLUDA_BRIDGE_BUILD)"
	mkdir -p "$(ZLUDA_BRIDGE_BUILD)/downloads"
	python3 "$(ZLUDA_BRIDGE_SCRIPTS)/inputs.py" bridge-check "$(SRCDIR)"
	python3 "$(ZLUDA_BRIDGE_SCRIPTS)/inputs.py" fetch "$(ZLUDA_BRIDGE_BUILD)/downloads" --group bridges
	env BUILD_ROOT="$(ZLUDA_BRIDGE_BUILD)" DOWNLOAD_ROOT="$(ZLUDA_BRIDGE_BUILD)/downloads" BUILD_PART=bridges bash "$(ZLUDA_BRIDGE_SCRIPTS)/install-rust.sh"
	python3 "$(ZLUDA_BRIDGE_SCRIPTS)/wine_sdk.py" "$(ZLUDA_BRIDGE_BUILD)" "$(ZLUDA_BRIDGE_SDK)" --generated \
	    --object "$(WINE_x86_64_OBJ)" --source "$(WINE_SRC)"
	# Build from the immutable checkout: path dependencies and Git provenance
	# require the complete ZLUDA source layout, rather than a lone rsynced crate.
	env BRIDGE_SOURCE="$(SRCDIR)/zluda/wine/zluda_wine" BUILD_ROOT="$(ZLUDA_BRIDGE_BUILD)" BRIDGE_DEST="$(ZLUDA_BRIDGE_PAYLOAD)" \
	    PATH="$(ZLUDA_BRIDGE_BUILD)/rust/bin:$(PATH)" CARGO_HOME="$(ZLUDA_BRIDGE_BUILD)/cargo" \
	    WINE_SDK_ROOT="$(ZLUDA_BRIDGE_SDK)" WINE_SOURCE_ROOT="$(WINE_SRC)" JOBS=3 bash "$(ZLUDA_BRIDGE_SCRIPTS)/build-bridge.sh"
	touch $@

# Both Wine dist targets must finish before removing obsolete canonical bridge files.
$(OBJ)/.zluda-bridge-dist: $(OBJ)/.zluda-bridge-build $(OBJ)/.wine-x86_64-dist $(OBJ)/.wine-i386-dist
	install -d "$(DST_LIBDIR)/wine-cuda" "$(DST_BASE)/provenance" "$(DST_BASE)/sources"
	cp -a "$(ZLUDA_BRIDGE_PAYLOAD)/files/lib/wine-cuda/." "$(DST_LIBDIR)/wine-cuda/"
	cp -a "$(ZLUDA_BRIDGE_PAYLOAD)/provenance/." "$(DST_BASE)/provenance/"
	python3 "$(ZLUDA_BRIDGE_SCRIPTS)/cuda_package.py" archive-stubs "$(DST_BASE)" --archive "$(ZLUDA_BRIDGE_BUILD)/downloads/GE-Proton11-7-x86_64.tar.gz"
	python3 "$(ZLUDA_BRIDGE_SCRIPTS)/cuda_package.py" clean "$(DST_BASE)"
	env CARGO_HOME="$(ZLUDA_BRIDGE_BUILD)/cargo" python3 "$(ZLUDA_BRIDGE_SCRIPTS)/cuda_package.py" licenses "$(DST_BASE)" --source "$(SRCDIR)/zluda"
	env CARGO_HOME="$(ZLUDA_BRIDGE_BUILD)/cargo" PATH="$(ZLUDA_BRIDGE_BUILD)/rust/bin:$(PATH)" WINE_SOURCE_ROOT="$(WINE_SRC)" WINE_SDK_ROOT="$(ZLUDA_BRIDGE_SDK)" \
	    python3 "$(ZLUDA_BRIDGE_SCRIPTS)/source_archive.py" --source "$(SRCDIR)/zluda" \
	    --build "$(ZLUDA_BRIDGE_BUILD)/zluda-wine/target" --output "$(DST_BASE)/sources/zluda-wine.tar.gz"
	rm -rf "$(DST_BASE)/sources/overlay-recipes"
	mkdir -p "$(DST_BASE)/sources/overlay-recipes"
	git -C "$(SRCDIR)" archive HEAD packaging/zluda-overlay | tar -x --strip-components=2 -C "$(DST_BASE)/sources/overlay-recipes"
	touch $@

zluda-bridge-build: $(OBJ)/.zluda-bridge-build
zluda-bridge zluda-bridge-dist: $(OBJ)/.zluda-bridge-dist
all-build: zluda-bridge-build
all all-dist default_pfx: zluda-bridge-dist
.PHONY: zluda-bridge zluda-bridge-build zluda-bridge-dist

zluda-bridge-clean::
	rm -rf "$(ZLUDA_BRIDGE_BUILD)" "$(OBJ)/.zluda-bridge-build" "$(OBJ)/.zluda-bridge-dist"

endif
