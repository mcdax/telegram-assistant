from telegram_assistant.loop_protection import LoopProtection


def test_initially_not_our_write():
    lp = LoopProtection()
    assert lp.is_our_write(chat_id=1, text="hello") is False


def test_record_then_match():
    lp = LoopProtection()
    lp.record(chat_id=1, text="hello")
    assert lp.is_our_write(chat_id=1, text="hello") is True


def test_record_does_not_match_different_text():
    lp = LoopProtection()
    lp.record(chat_id=1, text="hello")
    assert lp.is_our_write(chat_id=1, text="world") is False


def test_independent_chats():
    lp = LoopProtection()
    lp.record(chat_id=1, text="hello")
    assert lp.is_our_write(chat_id=2, text="hello") is False
