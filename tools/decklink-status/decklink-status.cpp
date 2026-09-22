// nexrec-decklink-status — SDI input and reference status for every DeckLink
// sub-device, as JSON on stdout.
//
// The DeckLink Status API only reports input signal lock on a sub-device that
// has an active input stream. An idle connector reads "unlocked" even with
// signal present. For each sub-device this tool:
//
//   1. If the input can be opened, enable video input WITH format detection,
//      start streams, and wait briefly for a detected format / lock.
//   2. If the input is busy (nexrec-record's FFmpeg holds it — DeckLink
//      sub-devices are exclusive-open), fall back to IDeckLinkStatus, which
//      is valid while streams are active.
//
// Output (one object, NexVUE-compatible):
//   {"devices":[{"index":0,"name":"DeckLink Quad 2 (1)","input_locked":true,
//     "input_mode":"1080i59.94","reference_locked":false,
//     "reference_mode":"unknown","busy":false}]}
//
// Optional argv[1] limits the probe to one display name or iterator index.
// Index order matches `ffmpeg -f decklink -list_devices` (IDeckLinkIterator).
//
// Build: see Makefile. Requires the Blackmagic DeckLink SDK on the include
// path plus DeckLinkAPIDispatch.cpp. Not a media path — capture stays FFmpeg.

#include "DeckLinkAPI.h"
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>

static const int DETECT_TIMEOUT_MS = 700;

static std::string modeName(int64_t mode)
{
    switch ((BMDDisplayMode)mode) {
        case bmdModeNTSC:          return "525i59.94 (NTSC)";
        case bmdModePAL:           return "625i50 (PAL)";
        case bmdModeHD720p50:      return "720p50";
        case bmdModeHD720p5994:    return "720p59.94";
        case bmdModeHD720p60:      return "720p60";
        case bmdModeHD1080i50:     return "1080i50";
        case bmdModeHD1080i5994:   return "1080i59.94";
        case bmdModeHD1080i6000:   return "1080i60";
        case bmdModeHD1080p2398:   return "1080p23.98";
        case bmdModeHD1080p24:     return "1080p24";
        case bmdModeHD1080p25:     return "1080p25";
        case bmdModeHD1080p2997:   return "1080p29.97";
        case bmdModeHD1080p30:     return "1080p30";
        case bmdModeHD1080p50:     return "1080p50";
        case bmdModeHD1080p5994:   return "1080p59.94";
        case bmdModeHD1080p6000:   return "1080p60";
        case bmdMode2k2398:        return "2K 23.98";
        case bmdMode4K2160p2997:   return "2160p29.97";
        case bmdMode4K2160p5994:   return "2160p59.94";
        default: {
            if (mode == 0) return "unknown";
            char fourcc[5] = {
                (char)((mode >> 24) & 0xFF), (char)((mode >> 16) & 0xFF),
                (char)((mode >> 8) & 0xFF),  (char)(mode & 0xFF), 0 };
            return std::string("fourcc:") + fourcc;
        }
    }
}

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

class DetectCallback : public IDeckLinkInputCallback {
public:
    std::mutex mtx;
    std::condition_variable cv;
    std::atomic<bool> detected{false};
    int64_t detectedMode{0};

    HRESULT STDMETHODCALLTYPE VideoInputFormatChanged(
        BMDVideoInputFormatChangedEvents /*events*/,
        IDeckLinkDisplayMode* newMode,
        BMDDetectedVideoInputFormatFlags /*flags*/) override
    {
        if (newMode) {
            {
                std::lock_guard<std::mutex> lk(mtx);
                detectedMode = (int64_t)newMode->GetDisplayMode();
                detected = true;
            }
            cv.notify_all();
        }
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE VideoInputFrameArrived(
        IDeckLinkVideoInputFrame* frame,
        IDeckLinkAudioInputPacket* /*audio*/) override
    {
        if (frame && !(frame->GetFlags() & bmdFrameHasNoInputSource)) {
            {
                std::lock_guard<std::mutex> lk(mtx);
                detected = true;
            }
            cv.notify_all();
        }
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID, void**) override { return E_NOINTERFACE; }
    ULONG   STDMETHODCALLTYPE AddRef()  override { return 1; }
    ULONG   STDMETHODCALLTYPE Release() override { return 1; }
};

// Returns true if the input was opened. busy=true when EnableVideoInput fails
// (typically the recorder already holds the sub-device).
static bool probeActive(IDeckLink* deckLink, bool& locked, int64_t& mode, bool& busy)
{
    locked = false; mode = 0; busy = false;

    IDeckLinkInput* input = nullptr;
    if (deckLink->QueryInterface(IID_IDeckLinkInput, (void**)&input) != S_OK || !input)
        return false;

    DetectCallback cb;
    input->SetCallback(&cb);

    HRESULT hr = input->EnableVideoInput(
        bmdModeHD1080i5994, bmdFormat8BitYUV, bmdVideoInputEnableFormatDetection);
    if (hr != S_OK) {
        busy = true;
        input->SetCallback(nullptr);
        input->Release();
        return false;
    }

    if (input->StartStreams() == S_OK) {
        std::unique_lock<std::mutex> lk(cb.mtx);
        cb.cv.wait_for(lk, std::chrono::milliseconds(DETECT_TIMEOUT_MS),
                       [&]{ return cb.detected.load(); });
        locked = cb.detected.load();
        mode = cb.detectedMode;
        lk.unlock();
        input->StopStreams();
    }

    if (locked && mode == 0) {
        IDeckLinkStatus* st = nullptr;
        if (deckLink->QueryInterface(IID_IDeckLinkStatus, (void**)&st) == S_OK) {
            int64_t v = 0;
            if (st->GetInt(bmdDeckLinkStatusDetectedVideoInputMode, &v) == S_OK)
                mode = v;
            st->Release();
        }
    }

    input->DisableVideoInput();
    input->SetCallback(nullptr);
    input->Release();
    return true;
}

static void probeStatusFlag(IDeckLink* deckLink, bool& locked, int64_t& mode)
{
    locked = false; mode = 0;
    IDeckLinkStatus* status = nullptr;
    if (deckLink->QueryInterface(IID_IDeckLinkStatus, (void**)&status) == S_OK) {
        bool b = false; int64_t v = 0;
        if (status->GetFlag(bmdDeckLinkStatusVideoInputSignalLocked, &b) == S_OK)
            locked = b;
        if (status->GetInt(bmdDeckLinkStatusDetectedVideoInputMode, &v) == S_OK)
            mode = v;
        status->Release();
    }
}

static void readReference(IDeckLink* deckLink, bool& refLocked, int64_t& refMode)
{
    refLocked = false; refMode = 0;
    IDeckLinkStatus* status = nullptr;
    if (deckLink->QueryInterface(IID_IDeckLinkStatus, (void**)&status) == S_OK) {
        bool b = false; int64_t v = 0;
        if (status->GetFlag(bmdDeckLinkStatusReferenceSignalLocked, &b) == S_OK)
            refLocked = b;
        if (status->GetInt(bmdDeckLinkStatusReferenceSignalMode, &v) == S_OK)
            refMode = v;
        status->Release();
    }
}

static bool selected(const std::string& filter, int index, const std::string& name)
{
    if (filter.empty()) return true;
    if (filter == name) return true;
    if (filter == std::to_string(index)) return true;
    return false;
}

int main(int argc, char** argv)
{
    std::string filter;
    if (argc >= 2 && argv[1] && argv[1][0] != '\0')
        filter = argv[1];

    IDeckLinkIterator* iterator = CreateDeckLinkIteratorInstance();
    if (!iterator) {
        fprintf(stderr, "DeckLink drivers not installed or no API available\n");
        printf("{\"devices\":[],\"error\":\"no_decklink_api\"}\n");
        return 1;
    }

    printf("{\"devices\":[");

    IDeckLink* deckLink = nullptr;
    int index = 0;
    bool first = true;

    while (iterator->Next(&deckLink) == S_OK) {
        const char* name = nullptr;
        std::string displayName = "unknown";
        if (deckLink->GetDisplayName(&name) == S_OK && name) {
            displayName = name;
            free((void*)name);
        }

        bool want = selected(filter, index, displayName);
        bool inputLocked = false, busy = false;
        int64_t inputMode = 0;
        bool refLocked = false;
        int64_t refMode = 0;

        if (want) {
            if (!probeActive(deckLink, inputLocked, inputMode, busy))
                probeStatusFlag(deckLink, inputLocked, inputMode);
            readReference(deckLink, refLocked, refMode);

            printf("%s{\"index\":%d,\"name\":\"%s\","
                   "\"input_locked\":%s,\"input_mode\":\"%s\","
                   "\"reference_locked\":%s,\"reference_mode\":\"%s\","
                   "\"busy\":%s}",
                   first ? "" : ",",
                   index,
                   jsonEscape(displayName.c_str()).c_str(),
                   inputLocked ? "true" : "false",
                   modeName(inputLocked ? inputMode : 0).c_str(),
                   refLocked ? "true" : "false",
                   modeName(refLocked ? refMode : 0).c_str(),
                   busy ? "true" : "false");
            first = false;
        }

        index++;
        deckLink->Release();
    }

    printf("]}\n");
    iterator->Release();
    return 0;
}
