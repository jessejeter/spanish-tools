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
 *
 * SRS sheet column layout (v2):
 *   A: word
 *   B: reviews (JSON string — array of {date, passed})
 *   C: lastReview
 *   D: retired
 *
 * NOTE: Clear existing SRS sheet rows before deploying this version,
 * or old rows will be silently ignored (cards get a fresh start).
 */

const SRS_SHEET_NAME = 'SRS';

// Runs when the spreadsheet opens — adds menu and refreshes Sheet2 col A.
function onOpen() {
  SpreadsheetApp.getActiveSpreadsheet().addMenu('Vocab Tools', [
    { name: 'Refresh Sheet2 after sync', functionName: 'populateSheet2ColA' },
  ]);
  populateSheet2ColA();
}

function populateSheet2ColA() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const s1 = ss.getSheetByName('Sheet1');
  const s2 = ss.getSheetByName('Sheet2');
  if (!s1 || !s2) return;

  const numRows = s1.getLastRow() - 1;
  if (numRows < 1) return;

  const s1Vals = s1.getRange(2, 1, numRows, 6).getValues();

  const newColA = [];
  for (let i = 0; i < numRows; i++) {
    const spanish = s1Vals[i][1] || '';
    const english = s1Vals[i][2] || '';
    const sense   = s1Vals[i][5] || '';
    const line1   = sense ? `${spanish}: ${english} (${sense})` : `${spanish}: ${english}`;

    if (!spanish) { newColA.push(['']); continue; }

    const dateVal = s1Vals[i][0];
    const date = dateVal ? Utilities.formatDate(new Date(dateVal), Session.getScriptTimeZone(), 'M/d/yyyy') : '';
    const pos  = s1Vals[i][3] || '';
    const pop  = s1Vals[i][4] || '';
    newColA.push([`${line1}\n\n${date}\n\nPOS: ${pos}\n\nPop: ${pop}`]);
  }

  s2.getRange(2, 1, numRows, 1).setValues(newColA);
}

// Auto-populate column D with today's date when the Reviewed checkbox (col C) is checked.
function onEdit(e) {
  const sheet = e.range.getSheet();
  if (sheet.getName() !== 'Sheet2' || e.range.getColumn() !== 3 || e.range.getRow() < 2) return;
  const row = e.range.getRow();
  if (e.value === 'TRUE') {
    // Write review date to col D
    const today = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'M/d/yyyy');
    sheet.getRange(row, 4).setValue(today);
  } else {
    sheet.getRange(row, 4).clearContent();
  }
}

function getSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  return ss.getSheetByName(SRS_SHEET_NAME) || ss.insertSheet(SRS_SHEET_NAME);
}

// Safely format a value that Google Sheets may have auto-converted to a Date
function fmtDate(v) {
  if (!v) return '';
  if (v instanceof Date) return Utilities.formatDate(v, 'UTC', "yyyy-MM-dd'T'HH:mm:ss'Z'");
  return String(v);
}

// GET — return all SRS data as JSON
// Col A: word, Col B: reviews JSON, Col C: lastReview, Col D: retired
function doGet() {
  const sheet = getSheet();
  const rows = sheet.getDataRange().getValues();
  const srs = {};
  for (const [word, reviewsJson, lastReview, retired] of rows) {
    if (!word) continue;
    let reviews = [];
    try { reviews = JSON.parse(reviewsJson) || []; } catch {}
    srs[String(word)] = {
      reviews,
      lastReview: fmtDate(lastReview),
      retired: retired === true || String(retired).toUpperCase() === 'TRUE',
    };
  }
  return ContentService
    .createTextOutput(JSON.stringify(srs))
    .setMimeType(ContentService.MimeType.JSON);
}

// POST — upsert SRS entries
// Body: JSON array of { word, reviews, lastReview, retired }
function doPost(e) {
  const updates = JSON.parse(e.postData.contents);
  const sheet = getSheet();
  const rows = sheet.getDataRange().getValues();

  // Build word → 1-indexed row map
  const rowMap = {};
  rows.forEach((r, i) => { if (r[0]) rowMap[String(r[0])] = i + 1; });

  for (const u of updates) {
    const row = [u.word, JSON.stringify(u.reviews || []), u.lastReview || '', u.retired || false];
    if (rowMap[u.word]) {
      sheet.getRange(rowMap[u.word], 1, 1, 4).setValues([row]);
    } else {
      sheet.appendRow(row);
      rowMap[u.word] = sheet.getLastRow();
    }
  }

  return ContentService
    .createTextOutput(JSON.stringify({ ok: true }))
    .setMimeType(ContentService.MimeType.JSON);
}
