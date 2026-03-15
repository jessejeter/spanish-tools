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
 * SRS sheet column layout (v3):
 *   A: word
 *   B: data (JSON object — {reviews, right, wrong, firstReview})
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

function getSheet(name) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheetName = name || SRS_SHEET_NAME;
  return ss.getSheetByName(sheetName) || ss.insertSheet(sheetName);
}

// Safely format a value that Google Sheets may have auto-converted to a Date
function fmtDate(v) {
  if (!v) return '';
  if (v instanceof Date) return Utilities.formatDate(v, 'UTC', "yyyy-MM-dd'T'HH:mm:ss'Z'");
  return String(v);
}

// GET — return all SRS data as JSON
// Optional query param ?sheet=FramesSRS to read from a different sheet
function doGet(e) {
  const sheet = getSheet(e && e.parameter && e.parameter.sheet);
  if (sheet.getLastRow() === 0) {
    return ContentService.createTextOutput('{}').setMimeType(ContentService.MimeType.JSON);
  }
  const rows = sheet.getDataRange().getValues();
  const srs = {};
  for (const [word, dataJson, lastReview, retired] of rows) {
    if (!word) continue;
    let reviews = [], right = 0, wrong = 0, firstReview = '';
    try {
      const parsed = JSON.parse(dataJson);
      if (Array.isArray(parsed)) {
        reviews = parsed;
        right = parsed.filter(r => r.passed).length;
        wrong = parsed.filter(r => !r.passed).length;
        firstReview = parsed.length > 0 ? (parsed[0].date || '') : '';
      } else if (parsed) {
        reviews = parsed.reviews || [];
        right = parsed.right || 0;
        wrong = parsed.wrong || 0;
        firstReview = parsed.firstReview || '';
      }
    } catch {}
    srs[String(word)] = {
      reviews, right, wrong, firstReview,
      lastReview: fmtDate(lastReview),
      retired: retired === true || String(retired).toUpperCase() === 'TRUE',
    };
  }
  return ContentService
    .createTextOutput(JSON.stringify(srs))
    .setMimeType(ContentService.MimeType.JSON);
}

// POST — upsert SRS entries
// Body: JSON array of { word, reviews, right, wrong, firstReview, lastReview, retired }
// Words prefixed with "frame:" are routed to the FramesSRS sheet automatically.
function doPost(e) {
  const updates = JSON.parse(e.postData.contents);

  // Group updates by destination sheet based on word prefix
  const bySheet = {};
  for (const u of updates) {
    const sheetName = String(u.word).startsWith('frame:') ? 'FramesSRS' : SRS_SHEET_NAME;
    (bySheet[sheetName] = bySheet[sheetName] || []).push(u);
  }

  for (const [sheetName, sheetUpdates] of Object.entries(bySheet)) {
    upsertRows(getSheet(sheetName), sheetUpdates);
  }

  return ContentService
    .createTextOutput(JSON.stringify({ ok: true }))
    .setMimeType(ContentService.MimeType.JSON);
}

function upsertRows(sheet, updates) {
  const rowMap = {};
  if (sheet.getLastRow() > 0) {
    sheet.getDataRange().getValues().forEach((r, i) => { if (r[0]) rowMap[String(r[0])] = i + 1; });
  }
  for (const u of updates) {
    const dataJson = JSON.stringify({
      reviews: u.reviews || [],
      right: u.right || 0,
      wrong: u.wrong || 0,
      firstReview: u.firstReview || '',
    });
    const row = [u.word, dataJson, u.lastReview || '', u.retired || false];
    if (rowMap[u.word]) {
      sheet.getRange(rowMap[u.word], 1, 1, 4).setValues([row]);
    } else {
      sheet.appendRow(row);
      rowMap[u.word] = sheet.getLastRow();
    }
  }
}
