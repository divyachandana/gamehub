// NightWatch door/window contact for the demo.
// Wiring: magnetic reed switch between pin 2 and GND (uses the internal pull-up).
//   magnet next to switch = door closed (pin LOW); magnet away = door open (pin HIGH).
// Prints one line per change over USB serial at 115200 baud, e.g. "DOOR front OPEN".
// nightwatch/bridges/door_serial.py reads these lines and posts them to the local server.

const int REED_PIN = 2;
const char* EXIT_ID = "front";   // must match an exit id in nightwatch/demo/facility.json
const unsigned long DEBOUNCE_MS = 60;

int lastState = -1;
unsigned long changedAt = 0;

void setup() {
  pinMode(REED_PIN, INPUT_PULLUP);
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
}

void loop() {
  int state = digitalRead(REED_PIN);
  if (state != lastState && millis() - changedAt > DEBOUNCE_MS) {
    changedAt = millis();
    lastState = state;
    bool open = (state == HIGH);
    digitalWrite(LED_BUILTIN, open ? HIGH : LOW);
    Serial.print("DOOR ");
    Serial.print(EXIT_ID);
    Serial.println(open ? " OPEN" : " CLOSED");
  }
}
