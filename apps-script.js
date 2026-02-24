/**
 * Google Apps Script — Spanish Flashcards SRS sync
 *
 * Setup:
 *   1. Open the spreadsheet
 *   2. Extensions > Apps Script
 *   3. Replace any existing code with this file's contents
 *   4. Click Deploy > New deployment
 *      - Type: Web app
 *      - Execute as: Me
 *      - Who has access: Anyone
 *   5. Authorize and copy the Web app URL
 *   6. Paste it into index.html as APPS_SCRIPT_URL
 *
 * Re-deploying after edits: Deploy > Manage deployments > edit the existing one.
 */

const SRS_SHEET_NAME = 'SRS';

// Auto-populate column D with today's date when the Reviewed checkbox (col C) is checked.
function onEdit(e) {
  const sheet = e.range.getSheet();
  if (sheet.getName() !== 'Sheet2' || e.range.getColumn() !== 3 || e.range.getRow() < 2) return;
  const row = e.range.getRow();
  if (e.value === 'TRUE') {
    // Write review date to col D
    const today = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'M/d/yyyy');
    sheet.getRange(row, 4).setValue(today);

    // Write summary to col A from Sheet1 data
    const s1 = e.source.getSheetByName('Sheet1').getRange(row, 1, 1, 5).getValues()[0];
    const date    = s1[0] ? Utilities.formatDate(new Date(s1[0]), Session.getScriptTimeZone(), 'M/d/yyyy') : '';
    const spanish = s1[1] || '';
    const english = s1[2] || '';
    const pos     = s1[3] || '';
    const pop     = s1[4] || '';
    const summary = `${date}\n\n${spanish}: ${english}\n\nPOS: ${pos}\n\nPop: ${pop}`;
    sheet.getRange(row, 1).setValue(summary);
  } else {
    sheet.getRange(row, 4).clearContent();
    sheet.getRange(row, 1).clearContent();
  }
}

function getSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  return ss.getSheetByName(SRS_SHEET_NAME) || ss.insertSheet(SRS_SHEET_NAME);
}

// Safely format a value that Google Sheets may have auto-converted to a Date
function fmtDate(v) {
  if (!v) return '';
  if (v instanceof Date) return Utilities.formatDate(v, 'UTC', 'yyyy-MM-dd');
  return String(v);
}

// GET — return all SRS data as JSON
function doGet() {
  const sheet = getSheet();
  const rows = sheet.getDataRange().getValues();
  const srs = {};
  for (const [word, interval, easeFactor, nextReview, lastReview, retired] of rows) {
    if (!word) continue;
    srs[String(word)] = {
      interval:    Number(interval)    || 0,
      easeFactor:  Number(easeFactor)  || 2.5,
      nextReview:  fmtDate(nextReview),
      lastReview:  fmtDate(lastReview),
      retired:     retired === true || String(retired).toUpperCase() === 'TRUE',
    };
  }
  return ContentService
    .createTextOutput(JSON.stringify(srs))
    .setMimeType(ContentService.MimeType.JSON);
}

// POST — upsert SRS entries
// Body: JSON array of { word, interval, easeFactor, nextReview, lastReview, retired }
function doPost(e) {
  const updates = JSON.parse(e.postData.contents);
  const sheet = getSheet();
  const rows = sheet.getDataRange().getValues();

  // Build word → 1-indexed row map
  const rowMap = {};
  rows.forEach((r, i) => { if (r[0]) rowMap[String(r[0])] = i + 1; });

  for (const u of updates) {
    const row = [u.word, u.interval, u.easeFactor, u.nextReview, u.lastReview, u.retired || false];
    if (rowMap[u.word]) {
      sheet.getRange(rowMap[u.word], 1, 1, 6).setValues([row]);
    } else {
      sheet.appendRow(row);
      rowMap[u.word] = sheet.getLastRow();
    }
  }

  return ContentService
    .createTextOutput(JSON.stringify({ ok: true }))
    .setMimeType(ContentService.MimeType.JSON);
}
