#pragma once

#include <cstdint>
#include <functional>
#include <string_view>

class Flex;
class Node;
class Renderer;
class Button;

enum class LockscreenKeyboardActionType : std::uint8_t {
  Character,
  Backspace,
  Submit,
};

struct LockscreenKeyboardAction {
  LockscreenKeyboardActionType type;
  char character = '\0';
};

class LockscreenKeyboardModel {
public:
  using ActionCallback = std::function<void(LockscreenKeyboardAction)>;

  void setActionCallback(ActionCallback callback);
  void show() noexcept;
  void hide() noexcept;
  void reset() noexcept;
  void toggleShift() noexcept;
  void showSymbols() noexcept;
  void showLetters() noexcept;
  void typeCharacter(char character);
  void backspace();
  void submit();

  [[nodiscard]] bool visible() const noexcept { return m_visible; }
  [[nodiscard]] bool symbolMode() const noexcept { return m_symbolMode; }
  [[nodiscard]] bool oneShotShift() const noexcept { return m_oneShotShift; }

private:
  bool m_visible = false;
  bool m_symbolMode = false;
  bool m_oneShotShift = false;
  ActionCallback m_actionCallback;
};

class LockscreenKeyboard {
public:
  explicit LockscreenKeyboard(Node& parent);

  void setActionCallback(LockscreenKeyboardModel::ActionCallback callback);
  void show();
  void hide();
  void reset();
  void arrange(Renderer& renderer, float width, float height);

  [[nodiscard]] bool visible() const noexcept { return m_model.visible(); }

private:
  void addCharacterRow(Flex& parent, std::string_view characters);
  void addControlRow(Flex& parent, bool symbols);
  void syncState();

  LockscreenKeyboardModel m_model;
  Flex* m_root = nullptr;
  Flex* m_letterRows = nullptr;
  Flex* m_symbolRows = nullptr;
  Button* m_shiftButton = nullptr;
};
