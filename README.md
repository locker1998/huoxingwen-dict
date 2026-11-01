# 火星文

Local helper for adding missing 字／詞 to a 小狼毫 table dictionary.

```
copy settings.example.json settings.json
py -3 app.py
```

http://127.0.0.1:8765/

`settings.json`, `skipped.json`, and work files under `files/` stay local and are not committed. Empty `dict_path` uses `%APPDATA%\Rime\locker.dict.yaml`.

左側圖示列：分析、編碼、處理、待寫入，最下為設定。

**分析**：單一面板，手動／匯入切換。手動輸入或貼上原文；匯入則瀏覽檔案。有 `raw_text` + `metadata` 的工作 JSON 直接載入；其他 JSON／XML 等鍵值格式會詢問要用哪些欄位當作 `raw_text`（固定高度清單、可多選；含全部鍵與值／僅全部鍵／僅全部值，這三項與其他選項互斥）。純文字才當原文。JSON／XML 解析失敗不會改當 txt 讀。不從工作資料夾點選。不綁定任何聊天平台。

## JSON

```json
{
  "raw_text": "房間裡有人",
  "metadata": {
    "source": "notes",
    "date": "2026-11-01",
    "extracted": false,
    "extracted_text": [],
    "codes": {},
    "implemented": false
  }
}
```

| 旗標 | 意義 |
|------|------|
| `extracted` | 已從 `raw_text` 抽出未在字典裡的字／詞（可以是空陣列：原文用字都已在字典） |

擷取規則：先列不在字典的單字。再扣掉已有詞（最長匹配、至少二字），剩下的漢字段由左到右每兩字一詞；多出來的單字只當字、不加整句、不切滑動窗口。不維護助詞表。
| `implemented` | 已成功寫入字典檔 |

沒有 `processed`。是否處理完看 `extracted_text` 與 `codes` 的數量：

- 編碼數 ≤ 擷取數 → 未完成（進「處理」）
- 擷取項數 = 編碼數，且每一項都有鍵（值可為空＝跳過）→ 可寫入（進「待寫入」；若擷取為空也算齊全）。寫入字典時略過空編碼，並把跳過的字／詞記入略過清單；之後分析不再抽出已略過項。處理裡跳過尚未寫入前不算記住。
- 編碼數 > 擷取數 → 無效，必須選：手動修正、清除全部編碼、從頭刪除超出、從尾刪除超出

JSON 放在設定裡的**工作資料夾**（預設 `files/`）。設定可開「處理完成後自動寫入字典」與「寫入字典後自動重新部署」（呼叫 小狼毫 `WeaselDeployer /deploy`）。
