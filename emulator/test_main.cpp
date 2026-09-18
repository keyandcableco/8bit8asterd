// Native harness: drives the emulator and writes a WAV so the output can be
#include <cstdint>
// checked by ear and by analysis.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <string>

extern "C" {
  void emu_init(double rate);
  void emu_render(float *out, int n);
  void emu_note_on(int ch, int note, int vel);
  void emu_note_off(int ch, int note);
  void emu_send_line(const char *line);
  const char *emu_read_lines();
  int emu_reg(int chip, int reg);
}

static void writeWav(const char *path, const std::vector<float> &s, int rate) {
  FILE *f = fopen(path, "wb");
  int n = (int)s.size();
  int dataBytes = n * 2;
  auto u32 = [&](uint32_t v){ fwrite(&v, 4, 1, f); };
  auto u16 = [&](uint16_t v){ fwrite(&v, 2, 1, f); };
  fwrite("RIFF", 1, 4, f); u32(36 + dataBytes); fwrite("WAVE", 1, 4, f);
  fwrite("fmt ", 1, 4, f); u32(16); u16(1); u16(1);
  u32(rate); u32(rate * 2); u16(2); u16(16);
  fwrite("data", 1, 4, f); u32(dataBytes);
  for (float v : s) {
    if (v > 1) v = 1; if (v < -1) v = -1;
    int16_t q = (int16_t)(v * 32767.0f * 0.9f);
    fwrite(&q, 2, 1, f);
  }
  fclose(f);
}

int main(int argc, char **argv) {
  const int RATE = 44100;
  emu_init(RATE);

  std::vector<float> out;
  auto render = [&](double seconds){
    int n = (int)(seconds * RATE);
    std::vector<float> buf(n);
    emu_render(buf.data(), n);
    out.insert(out.end(), buf.begin(), buf.end());
  };

  render(0.05);
  printf("boot reply: %s", emu_read_lines());

  // a plain C major triad
  emu_note_on(0, 60, 100); emu_note_on(0, 64, 100); emu_note_on(0, 67, 100);
  render(0.6);
  emu_note_off(0, 60); emu_note_off(0, 64); emu_note_off(0, 67);
  render(0.25);

  // drums on channel 10
  for (int i = 0; i < 4; i++) {
    emu_note_on(9, 36, 110); render(0.12);
    emu_note_on(9, 42, 90);  render(0.12);
    emu_note_on(9, 38, 110); render(0.12);
    emu_note_on(9, 42, 90);  render(0.12);
  }

  if (argc > 1) { writeWav(argv[1], out, RATE); printf("wrote %s (%.2fs)\n", argv[1], out.size()/(double)RATE); }
  return 0;
}
