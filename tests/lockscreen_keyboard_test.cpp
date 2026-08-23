#include "shell/lockscreen/lockscreen_keyboard.h"
#include "shell/lockscreen/lockscreen_login_box.h"

#include <print>
#include <string>
#include <unordered_map>
#include <variant>
#include <vector>

namespace {

  bool g_ok = true;

  void expect(bool condition, const std::string& message) {
    if (!condition) {
      std::println(stderr, "lockscreen_keyboard_test: FAIL: {}", message);
      g_ok = false;
    }
  }

} // namespace

int main() {
  LockscreenKeyboardModel model;
  std::vector<LockscreenKeyboardAction> actions;
  model.setActionCallback([&actions](LockscreenKeyboardAction action) { actions.push_back(action); });

  expect(!model.visible(), "default state is hidden");
  expect(!model.symbolMode(), "default state uses letters");
  expect(!model.oneShotShift(), "default Shift is off");

  model.show();
  model.hide();
  expect(!model.visible(), "hide clears visibility");
  model.show();
  model.toggleShift();
  expect(model.oneShotShift(), "Shift turns on");
  model.typeCharacter('a');
  expect(actions.back().type == LockscreenKeyboardActionType::Character && actions.back().character == 'A',
         "Shift uppercases one character");
  expect(!model.oneShotShift(), "Shift turns off after one character");

  model.toggleShift();
  model.toggleShift();
  expect(!model.oneShotShift(), "second Shift tap cancels Shift");

  model.showSymbols();
  expect(model.symbolMode(), "symbol mode turns on");
  model.typeCharacter('@');
  expect(actions.back().character == '@', "symbol character action is preserved");
  model.showLetters();
  expect(!model.symbolMode(), "ABC returns to letters");
  model.typeCharacter('b');
  expect(actions.back().type == LockscreenKeyboardActionType::Character && actions.back().character == 'b',
         "letter character emits its semantic action");

  model.backspace();
  expect(actions.back().type == LockscreenKeyboardActionType::Backspace, "Backspace emits its semantic action");
  model.submit();
  expect(actions.back().type == LockscreenKeyboardActionType::Submit, "Submit emits its semantic action");

  model.show();
  model.showSymbols();
  model.reset();
  expect(!model.visible() && !model.symbolMode() && !model.oneShotShift(), "reset restores all default state");

  std::unordered_map<std::string, WidgetSettingValue> settings;
  lockscreen_login_box::normalizeSettings(settings);
  expect(std::get<bool>(settings.at("show_osk_button")) == false, "normalization defaults OSK button off");
  settings.clear();
  lockscreen_login_box::applyAllDefaultSettings(settings);
  expect(std::get<bool>(settings.at("show_osk_button")) == false, "new settings default OSK button off");
  expect(!lockscreen_login_box::resolveStyle(settings).showOskButton, "resolved default hides OSK button");
  settings["show_osk_button"] = true;
  expect(lockscreen_login_box::resolveStyle(settings).showOskButton, "explicit setting shows OSK button");

  return g_ok ? 0 : 1;
}
