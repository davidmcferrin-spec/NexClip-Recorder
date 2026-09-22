# Build nexrec-decklink-status against the Blackmagic DeckLink SDK.
# The SDK is not vendored (license). Point SDK at the Linux package root
# or at the directory that contains DeckLinkAPI.h and DeckLinkAPIDispatch.cpp.
#
#   sudo apt-get install desktopvideo   # or the current Blackmagic .deb
#   make SDK=/path/to/decklink-sdk/include
#   sudo make install
#
# Ubuntu 24.04. Capture itself is FFmpeg -f decklink, not this binary.

SDK ?= /usr/include
CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -I$(SDK)
DISPATCH ?= $(SDK)/DeckLinkAPIDispatch.cpp
PREFIX ?= /usr/local

.PHONY: all clean install

all: nexrec-decklink-status

nexrec-decklink-status: decklink-status.cpp
	$(CXX) $(CXXFLAGS) -o $@ decklink-status.cpp $(DISPATCH) -ldl -lpthread

install: nexrec-decklink-status
	install -d $(DESTDIR)$(PREFIX)/bin
	install -m 755 nexrec-decklink-status $(DESTDIR)$(PREFIX)/bin/nexrec-decklink-status

clean:
	rm -f nexrec-decklink-status
