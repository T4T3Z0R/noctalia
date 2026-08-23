#include "shell/lockscreen/lockscreen_keyboard.h"

#include "render/core/renderer.h"
#include "render/scene/node.h"
#include "ui/builders.h"
#include "ui/controls/button.h"
#include "ui/controls/flex.h"
#include "ui/palette.h"
#include "ui/style.h"

#include <algorithm>
#include <cctype>
#include <string>
#include <utility>

void LockscreenKeyboardModel::setActionCallback(ActionCallback callback) { m_actionCallback = std::move(callback); }

void LockscreenKeyboardModel::show() noexcept { m_visible = true; }

void LockscreenKeyboardModel::hide() noexcept { m_visible = false; }

void LockscreenKeyboardModel::reset() noexcept {
  m_visible = false;
  m_symbolMode = false;
  m_oneShotShift = false;
}

void LockscreenKeyboardModel::toggleShift() noexcept {
  if (!m_symbolMode) {
    m_oneShotShift = !m_oneShotShift;
  }
}

void LockscreenKeyboardModel::showSymbols() noexcept {
  m_symbolMode = true;
  m_oneShotShift = false;
}

void LockscreenKeyboardModel::showLetters() noexcept {
  m_symbolMode = false;
  m_oneShotShift = false;
}

void LockscreenKeyboardModel::typeCharacter(char character) {
  char typed = character;
  if (!m_symbolMode && std::isalpha(static_cast<unsigned char>(character)) != 0) {
    typed = m_oneShotShift ? static_cast<char>(std::toupper(static_cast<unsigned char>(character))) : character;
    m_oneShotShift = false;
  }
  if (m_actionCallback) {
    m_actionCallback({LockscreenKeyboardActionType::Character, typed});
  }
}

void LockscreenKeyboardModel::backspace() {
  if (m_actionCallback) {
    m_actionCallback({LockscreenKeyboardActionType::Backspace});
  }
}

void LockscreenKeyboardModel::submit() {
  if (m_actionCallback) {
    m_actionCallback({LockscreenKeyboardActionType::Submit});
  }
}

namespace {

  Button* addKey(Flex& row, std::string text, float grow, std::function<void()> onClick) {
    auto key = ui::button({
        .text = std::move(text),
        .fontSize = Style::fontSizeBody,
        .variant = ButtonVariant::Secondary,
        .onClick = std::move(onClick),
        .configure = [grow](Button& button) {
          button.setControlHeight(52.0F);
          button.setFlexGrow(grow);
          button.setMinWidth(0.0F);
          button.inputArea()->setFocusable(false);
          button.setTabStop(false);
        },
    });
    auto* result = key.get();
    row.addChild(std::move(key));
    return result;
  }

  Flex* addRow(Flex& parent) {
    auto row = ui::flex(
        FlexDirection::Horizontal,
        {
            .align = FlexAlign::Stretch,
            .justify = FlexJustify::Center,
            .gap = Style::spaceXs,
            .widthPolicy = FlexSizePolicy::Fill,
            .heightPolicy = FlexSizePolicy::Fill,
            .flexGrow = 1.0F,
        }
    );
    auto* result = row.get();
    parent.addChild(std::move(row));
    return result;
  }

} // namespace

LockscreenKeyboard::LockscreenKeyboard(Node& parent) {
  auto root = ui::flex(
      FlexDirection::Vertical,
      {
          .out = &m_root,
          .align = FlexAlign::Stretch,
          .justify = FlexJustify::Center,
          .gap = Style::spaceXs,
          .paddingV = Style::spaceSm,
          .paddingH = Style::spaceSm,
          .fill = colorSpecFromRole(ColorRole::Surface, 0.96F),
          .widthPolicy = FlexSizePolicy::Fill,
          .heightPolicy = FlexSizePolicy::Fill,
          .visible = false,
          .participatesInLayout = false,
          .configure = [](Flex& flex) { flex.setZIndex(8); },
      }
  );

  m_root->addChild(ui::flex(FlexDirection::Vertical, {.out = &m_letterRows, .gap = Style::spaceXs, .flexGrow = 1.0F}));
  addCharacterRow(*m_letterRows, "qwertyuiop");
  addCharacterRow(*m_letterRows, "asdfghjkl");
  auto* letterControls = addRow(*m_letterRows);
  m_shiftButton = addKey(*letterControls, "⇧", 1.4F, [this]() {
    m_model.toggleShift();
    syncState();
  });
  for (const char character : std::string_view{"zxcvbnm"}) {
    addKey(*letterControls, std::string(1, static_cast<char>(std::toupper(character))), 1.0F, [this, character]() {
      m_model.typeCharacter(character);
      syncState();
    });
  }
  addKey(*letterControls, "⌫", 1.4F, [this]() { m_model.backspace(); });
  addControlRow(*m_letterRows, false);

  m_root->addChild(
      ui::flex(
          FlexDirection::Vertical, {.out = &m_symbolRows, .gap = Style::spaceXs, .flexGrow = 1.0F, .visible = false}
      )
  );
  addCharacterRow(*m_symbolRows, "1234567890");
  addCharacterRow(*m_symbolRows, "!@#$%^&*()");
  addCharacterRow(*m_symbolRows, "-_=+[]{}\\|");
  addCharacterRow(*m_symbolRows, ";:'\",.<>/?`~");
  addControlRow(*m_symbolRows, true);

  parent.addChild(std::move(root));
}

void LockscreenKeyboard::setActionCallback(LockscreenKeyboardModel::ActionCallback callback) {
  m_model.setActionCallback(std::move(callback));
}

void LockscreenKeyboard::show() {
  m_model.show();
  syncState();
}

void LockscreenKeyboard::hide() {
  m_model.hide();
  syncState();
}

void LockscreenKeyboard::reset() {
  m_model.reset();
  syncState();
}

void LockscreenKeyboard::arrange(Renderer& renderer, float width, float height) {
  const float keyboardHeight = std::min({height, 360.0F, std::max(304.0F, height * 0.38F)});
  m_root->arrange(renderer, LayoutRect{0.0F, height - keyboardHeight, width, keyboardHeight});
}

void LockscreenKeyboard::addCharacterRow(Flex& parent, std::string_view characters) {
  auto* row = addRow(parent);
  for (const char character : characters) {
    addKey(
        *row, std::string(1, static_cast<char>(std::toupper(static_cast<unsigned char>(character)))), 1.0F,
        [this, character]() {
          m_model.typeCharacter(character);
          syncState();
        }
    );
  }
}

void LockscreenKeyboard::addControlRow(Flex& parent, bool symbols) {
  auto* row = addRow(parent);
  addKey(*row, symbols ? "ABC" : "?123", 1.5F, [this, symbols]() {
    if (symbols) {
      m_model.showLetters();
    } else {
      m_model.showSymbols();
    }
    syncState();
  });
  if (!symbols) {
    addKey(*row, ",", 1.0F, [this]() { m_model.typeCharacter(','); });
  }
  addKey(*row, "SPACE", 4.0F, [this]() { m_model.typeCharacter(' '); });
  if (!symbols) {
    addKey(*row, ".", 1.0F, [this]() { m_model.typeCharacter('.'); });
  }
  if (symbols) {
    addKey(*row, "⌫", 1.5F, [this]() { m_model.backspace(); });
  }
  addKey(*row, "ENTER", 1.8F, [this]() { m_model.submit(); });
}

void LockscreenKeyboard::syncState() {
  m_root->setVisible(m_model.visible());
  m_letterRows->setVisible(!m_model.symbolMode());
  m_symbolRows->setVisible(m_model.symbolMode());
  m_shiftButton->setSelected(m_model.oneShotShift());
}
