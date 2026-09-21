#include <cstdint>
#include <cstdio>
#include "../parameters.h"
#include <vector>
#include <cmath>
#include <string>
extern "C" {
  void emu_init(double); void emu_render(float*,int);
  void emu_note_on(int,int,int); void emu_note_off(int,int);
  void emu_send_line(const char*); const char* emu_read_lines();
  int emu_reg(int,int);
}
// Reads back the tone period the firmware programmed for a held note, which
// is the pitch the chip will actually produce -- no DFT needed.
static int periodForNote(int note, int temperament, int root) {
  emu_init(44100);
  std::vector<float> buf(2048);
  char cmd[64];
  // Indices come from the generated header, never hardcoded: they shift
  // whenever a parameter is added, and this test silently passed nonsense
  // the first time that happened.
  snprintf(cmd, sizeof cmd, "P:%d:%d", P_TEMPERAMENT, temperament); emu_send_line(cmd);
  snprintf(cmd, sizeof cmd, "P:%d:%d", P_TEMPER_ROOT, root);        emu_send_line(cmd);
  emu_render(buf.data(), 2048);
  emu_note_on(0, note, 100);
  emu_render(buf.data(), 2048);
  for (int v = 0; v < 9; v++) {
    int lo = emu_reg(v % 3, 0 + (v / 3) * 2), hi = emu_reg(v % 3, 1 + (v / 3) * 2);
    int p = lo | ((hi & 0x0F) << 8);
    int amp = emu_reg(v % 3, 8 + (v / 3));
    if (p > 1 && amp != 0) return p;
  }
  return -1;
}
int main() {
  auto cents = [](double a, double b){ return 1200.0 * log2(b / a); };
  int failures = 0;

  // Expected third in cents, and how far off the AY's integer divisor can
  // drag it at this pitch. At C3/E3 one divisor step is about 4 cents, so
  // 6 is tight enough to catch a real regression without being flaky.
  struct Case { int id; const char *name; double expect; };
  const Case cases[] = {
    { 0, "Equal",    400.0 },
    { 2, "Just",     386.3 },
    { 1, "Meantone", 386.3 },
    { 6, "Vallotti", 392.2 },
  };
  const double TOLERANCE = 6.0;

  printf("major third C-E, measured from the programmed tone periods\n\n");
  for (const auto &c : cases) {
    int pc = periodForNote(48, c.id, 0);     // C3
    int pe = periodForNote(52, c.id, 0);     // E3
    if (pc < 0 || pe < 0) {
      printf("  %-9s FAILED to read a tone period\n", c.name);
      failures++;
      continue;
    }
    double fc = 1e6 / (16.0 * pc), fe = 1e6 / (16.0 * pe);
    double third = cents(fc, fe), err = fabs(third - c.expect);
    bool ok = err <= TOLERANCE;
    if (!ok) failures++;
    printf("  %-9s C=%7.2fHz E=%7.2fHz  third = %6.1f cents  (want %.1f +/-%.0f) %s\n",
           c.name, fc, fe, third, c.expect, TOLERANCE, ok ? "ok" : "FAIL");
  }

  if (failures) {
    printf("\ntemperament test: %d failure(s)\n", failures);
    return 1;
  }
  printf("\ntemperament test: all temperaments land within %.0f cents of theory\n", TOLERANCE);
  return 0;
}
