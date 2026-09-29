// nexrec-decklink-configure — set Duo 2 / Quad 2 connector mapping for capture
// without BlackmagicDesktopVideoSetup.
//
// Duo/Quad BNCs are bidirectional. Full-duplex ("SDI N & N+1") parks half the
// sub-devices and leaves partners as key/fill pairs. Capture needs an
// independent input on every connector → bmdProfileTwoSubDevicesHalfDuplex
// per profile group (SDK §2.4.11). A Quad 2 is two pairs, so that is four
// inputs. SetActive() persists to Desktop Video preferences.
//
// Usage:
//   nexrec-decklink-configure                 # JSON status (default)
//   nexrec-decklink-configure --status
//   nexrec-decklink-configure --apply-inputs  # half-duplex on all groups
//
// Stop nexrec-record@N before applying (exclusive device open). Exit codes:
//   0 ok, 1 API/driver missing, 2 apply failed for one or more groups,
//   64 bad args.
//
// Build: see Makefile. Not a media path — capture stays FFmpeg.

#include "DeckLinkAPI.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

static std::string jsonEscape(const char* s)
{
    std::string out;
    if (!s) return out;
    for (const char* p = s; *p; ++p) {
        if (*p == '"' || *p == '\\') { out += '\\'; out += *p; }
        else if (*p == '\n') out += "\\n";
        else out += *p;
    }
    return out;
}

static const char* profileIdName(int64_t id)
{
    switch ((BMDProfileID)id) {
        case bmdProfileOneSubDeviceFullDuplex:   return "one_full_duplex";
        case bmdProfileOneSubDeviceHalfDuplex:   return "one_half_duplex";
        case bmdProfileTwoSubDevicesFullDuplex:  return "two_full_duplex";
        case bmdProfileTwoSubDevicesHalfDuplex:  return "two_half_duplex";
        case bmdProfileFourSubDevicesHalfDuplex: return "four_half_duplex";
        default: return "unknown";
    }
}

static const char* duplexName(int64_t duplex)
{
    switch ((BMDDuplexMode)duplex) {
        case bmdDuplexFull:     return "full";
        case bmdDuplexHalf:     return "half";
        case bmdDuplexSimplex:  return "simplex";
        case bmdDuplexInactive: return "inactive";
        default:                return "unknown";
    }
}

struct DeviceInfo {
    int index = 0;
    std::string name;
    int64_t profileId = 0;
    bool hasProfileManager = false;
    bool profileReadable = false;
    int64_t duplex = 0;
    bool duplexReadable = false;
    int64_t deviceGroupId = 0;
    bool groupReadable = false;
    IDeckLink* deckLink = nullptr;
};

static void readAttrs(IDeckLink* deckLink, DeviceInfo& info)
{
    IDeckLinkProfileAttributes* pattrs = nullptr;
    if (deckLink->QueryInterface(IID_IDeckLinkProfileAttributes, (void**)&pattrs) == S_OK && pattrs) {
        int64_t v = 0;
        if (pattrs->GetInt(BMDDeckLinkProfileID, &v) == S_OK) {
            info.profileId = v;
            info.profileReadable = true;
        }
        if (pattrs->GetInt(BMDDeckLinkDuplex, &v) == S_OK) {
            info.duplex = v;
            info.duplexReadable = true;
        }
        if (pattrs->GetInt(BMDDeckLinkDeviceGroupID, &v) == S_OK) {
            info.deviceGroupId = v;
            info.groupReadable = true;
        }
        pattrs->Release();
    }

    IDeckLinkProfileManager* mgr = nullptr;
    if (deckLink->QueryInterface(IID_IDeckLinkProfileManager, (void**)&mgr) == S_OK && mgr) {
        info.hasProfileManager = true;
        mgr->Release();
    }
}

static bool activateHalfDuplex(IDeckLink* deckLink, std::string& err)
{
    IDeckLinkProfileManager* mgr = nullptr;
    if (deckLink->QueryInterface(IID_IDeckLinkProfileManager, (void**)&mgr) != S_OK || !mgr) {
        err = "no_profile_manager";
        return false;
    }

    // Two-subdevice half-duplex: Duo 2, and each Quad 2 connector pair.
    // Four-subdevice half-duplex is the 8K Pro fallback.
    const BMDProfileID candidates[] = {
        bmdProfileTwoSubDevicesHalfDuplex,
        bmdProfileFourSubDevicesHalfDuplex,
        bmdProfileOneSubDeviceHalfDuplex,
    };

    HRESULT last = E_FAIL;
    for (BMDProfileID want : candidates) {
        IDeckLinkProfile* profile = nullptr;
        last = mgr->GetProfile(want, &profile);
        if (last != S_OK || !profile)
            continue;

        bool active = false;
        if (profile->IsActive(&active) == S_OK && active) {
            profile->Release();
            mgr->Release();
            return true;
        }

        last = profile->SetActive();
        profile->Release();
        if (last == S_OK) {
            mgr->Release();
            return true;
        }
        err = "SetActive failed";
    }

    mgr->Release();
    if (err.empty())
        err = "half_duplex_profile_unavailable";
    return false;
}

static bool captureReady(const DeviceInfo& d)
{
    bool captureOk = d.duplexReadable
        && (d.duplex == (int64_t)bmdDuplexHalf || d.duplex == (int64_t)bmdDuplexSimplex);
    bool halfProfile = d.profileReadable
        && (d.profileId == (int64_t)bmdProfileTwoSubDevicesHalfDuplex
            || d.profileId == (int64_t)bmdProfileFourSubDevicesHalfDuplex
            || d.profileId == (int64_t)bmdProfileOneSubDeviceHalfDuplex);
    return captureOk || halfProfile;
}

static void printDevice(const DeviceInfo& d, bool first)
{
    printf("%s{\"index\":%d,\"name\":\"%s\"",
           first ? "" : ",",
           d.index,
           jsonEscape(d.name.c_str()).c_str());
    if (d.profileReadable) {
        printf(",\"profile\":\"%s\",\"profile_id\":%lld",
               profileIdName(d.profileId),
               (long long)d.profileId);
    } else {
        printf(",\"profile\":null,\"profile_id\":null");
    }
    if (d.duplexReadable)
        printf(",\"duplex\":\"%s\"", duplexName(d.duplex));
    else
        printf(",\"duplex\":null");
    printf(",\"configurable\":%s", d.hasProfileManager ? "true" : "false");
    if (d.groupReadable)
        printf(",\"device_group_id\":%lld", (long long)d.deviceGroupId);
    printf(",\"capture_ready\":%s}", captureReady(d) ? "true" : "false");
}

static void printStatus(const std::vector<DeviceInfo>& devices)
{
    printf("{\"devices\":[");
    bool first = true;
    for (const auto& d : devices) {
        printDevice(d, first);
        first = false;
    }
    printf("]}\n");
}

static void usage()
{
    fprintf(stderr,
            "Usage: nexrec-decklink-configure [--status | --apply-inputs]\n"
            "  --status         print JSON connector/profile status (default)\n"
            "  --apply-inputs   set Duo/Quad profile groups to half-duplex\n"
            "                   (one input per BNC; persists)\n"
            "Stop nexrec-record@N before --apply-inputs.\n");
}

int main(int argc, char** argv)
{
    bool apply = false;
    bool statusOnly = false;

    for (int i = 1; i < argc; ++i) {
        if (strcmp(argv[i], "--apply-inputs") == 0) {
            apply = true;
        } else if (strcmp(argv[i], "--status") == 0 || strcmp(argv[i], "-s") == 0) {
            statusOnly = true;
        } else if (strcmp(argv[i], "-h") == 0 || strcmp(argv[i], "--help") == 0) {
            usage();
            return 0;
        } else {
            usage();
            return 64;
        }
    }
    if (!apply)
        statusOnly = true;

    IDeckLinkIterator* iterator = CreateDeckLinkIteratorInstance();
    if (!iterator) {
        fprintf(stderr, "DeckLink drivers not installed or no API available\n");
        printf("{\"devices\":[],\"error\":\"no_decklink_api\"}\n");
        return 1;
    }

    std::vector<DeviceInfo> devices;
    IDeckLink* deckLink = nullptr;
    int index = 0;
    while (iterator->Next(&deckLink) == S_OK) {
        DeviceInfo info;
        info.index = index;
        info.deckLink = deckLink;

        const char* name = nullptr;
        if (deckLink->GetDisplayName(&name) == S_OK && name) {
            info.name = name;
            free((void*)name);
        } else {
            info.name = "unknown";
        }

        readAttrs(deckLink, info);
        devices.push_back(info);
        ++index;
    }
    iterator->Release();

    if (statusOnly && !apply) {
        printStatus(devices);
        for (auto& d : devices)
            d.deckLink->Release();
        return 0;
    }

    // Profile groups are connector pairs, not the whole card. Activating
    // either peer updates the pair; already-half devices are no-ops.
    int ok = 0;
    int fail = 0;
    int skipped = 0;

    printf("{\"actions\":[");
    bool firstAction = true;

    for (auto& d : devices) {
        if (!d.hasProfileManager) {
            ++skipped;
            continue;
        }

        std::string err;
        bool alreadyHalf = d.profileReadable
            && (d.profileId == (int64_t)bmdProfileTwoSubDevicesHalfDuplex
                || d.profileId == (int64_t)bmdProfileFourSubDevicesHalfDuplex
                || d.profileId == (int64_t)bmdProfileOneSubDeviceHalfDuplex);

        bool success = alreadyHalf || activateHalfDuplex(d.deckLink, err);

        printf("%s{\"index\":%d,\"name\":\"%s\",\"result\":\"%s\"",
               firstAction ? "" : ",",
               d.index,
               jsonEscape(d.name.c_str()).c_str(),
               success ? (alreadyHalf ? "already_half_duplex" : "activated") : "error");
        if (!success)
            printf(",\"error\":\"%s\"", jsonEscape(err.c_str()).c_str());
        printf("}");
        firstAction = false;

        if (success)
            ++ok;
        else
            ++fail;
    }

    printf("],\"ok\":%d,\"failed\":%d,\"skipped\":%d,\"devices\":[", ok, fail, skipped);

    for (auto& d : devices) {
        d.profileReadable = false;
        d.duplexReadable = false;
        d.groupReadable = false;
        d.hasProfileManager = false;
        readAttrs(d.deckLink, d);
    }
    bool first = true;
    for (const auto& d : devices) {
        printDevice(d, first);
        first = false;
    }
    printf("]}\n");

    for (auto& d : devices)
        d.deckLink->Release();

    if (fail > 0)
        return 2;
    return 0;
}
