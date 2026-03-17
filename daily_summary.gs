/**
 * Daily Task Summary Email — Google Apps Script
 *
 * Sends a 5 PM weekday email to info@capricorndrapery.com summarising
 * who submitted tasks, who didn't, and what work was done that day.
 *
 * SETUP
 * ─────
 * 1. In the Google Sheet, create a tab called "Staff".
 *    Put one employee full name per row in column A (no header row).
 * 2. Open Extensions → Apps Script, paste this entire file, and save.
 * 3. Run installTrigger() once from the editor (▶ button).
 *    Approve the authorisation prompts when asked.
 * 4. Done — the email will fire at 5 PM every weekday automatically.
 *
 * To test immediately, run sendDailySummary() from the editor.
 */

// ── Configuration ──────────────────────────────────────────────────
var CONFIG = {
  recipientEmail: "info@capricorndrapery.com",
  tabLog:         "DailyLog",
  tabStaff:       "Staff",
  timezone:       "America/Barbados",
  triggerHour:    17  // 5 PM
};

// ── Main function ──────────────────────────────────────────────────
function sendDailySummary() {
  var now = new Date();
  var day = Utilities.formatDate(now, CONFIG.timezone, "EEEE");

  // Skip weekends
  if (day === "Saturday" || day === "Sunday") return;

  var todayStr = Utilities.formatDate(now, CONFIG.timezone, "yyyy-MM-dd");
  var prettyDate = Utilities.formatDate(now, CONFIG.timezone, "MMM dd, yyyy");

  var ss = SpreadsheetApp.getActiveSpreadsheet();

  // ── Read staff list ──────────────────────────────────────────────
  var staffSheet = ss.getSheetByName(CONFIG.tabStaff);
  if (!staffSheet) {
    MailApp.sendEmail(
      CONFIG.recipientEmail,
      "Daily Task Summary — ERROR",
      'The "' + CONFIG.tabStaff + '" tab was not found in the spreadsheet. ' +
      "Please create it with employee names in column A."
    );
    return;
  }

  var staffData = staffSheet.getRange("A1:A" + staffSheet.getLastRow()).getValues();
  var allStaff = [];
  for (var i = 0; i < staffData.length; i++) {
    var name = String(staffData[i][0]).trim();
    if (name) allStaff.push(name);
  }

  if (allStaff.length === 0) {
    MailApp.sendEmail(
      CONFIG.recipientEmail,
      "Daily Task Summary — ERROR",
      'The "' + CONFIG.tabStaff + '" tab is empty. ' +
      "Please add employee names in column A."
    );
    return;
  }

  // ── Read today's log entries ─────────────────────────────────────
  var logSheet = ss.getSheetByName(CONFIG.tabLog);
  if (!logSheet || logSheet.getLastRow() < 2) {
    // No data at all
    var html = buildEmail(prettyDate, [], allStaff, allStaff, {}, 0);
    MailApp.sendEmail({
      to: CONFIG.recipientEmail,
      subject: "Daily Task Summary \u2014 " + prettyDate,
      htmlBody: html
    });
    return;
  }

  // DailyLog columns (1-indexed):
  //  A=timestamp_utc, B=date_local, C=employee, D=task_category,
  //  E=item_type, F=item_other_text, G=quantity, H=client_notes,
  //  I=task_other_text, J=submission_id
  var logData = logSheet.getDataRange().getValues();

  var submittedMap = {};   // employee → task count
  var categoryMap = {};    // category → { item → totalQty }
  var totalTasks = 0;

  for (var r = 1; r < logData.length; r++) {  // skip header row
    var dateCell = String(logData[r][1]).trim();
    if (dateCell !== todayStr) continue;

    var employee     = String(logData[r][2]).trim();
    var taskCategory = String(logData[r][3]).trim();
    var itemType     = String(logData[r][4]).trim();
    var itemOther    = String(logData[r][5]).trim();
    var quantity     = Number(logData[r][6]) || 0;

    // Use "Other" text if item is "Other"
    var displayItem = (itemType === "Other" && itemOther) ? itemOther : itemType;

    // Track submissions per employee
    submittedMap[employee] = (submittedMap[employee] || 0) + 1;
    totalTasks++;

    // Aggregate by category → item
    if (!categoryMap[taskCategory]) categoryMap[taskCategory] = {};
    var catItems = categoryMap[taskCategory];
    catItems[displayItem] = (catItems[displayItem] || 0) + quantity;
  }

  // ── Match log names to staff list (fuzzy) ─────────────────────────
  // Consolidate submission counts under the canonical staff name.
  // Match priority: exact → first-name → starts-with prefix.
  // A partial name only matches if it resolves to exactly one staff member.
  var staffCounts = {};  // canonical staff name → total task count
  for (var s = 0; s < allStaff.length; s++) staffCounts[allStaff[s]] = 0;

  for (var emp in submittedMap) {
    var matched = matchToStaff(emp, allStaff);
    if (matched) {
      staffCounts[matched] += submittedMap[emp];
    }
    // Unmatched names are ignored for the submitted/missing split
    // but their tasks still count toward totals and category summaries
  }

  var submittedStaff = [];
  var missingStaff = [];
  for (var s = 0; s < allStaff.length; s++) {
    if (staffCounts[allStaff[s]] > 0) {
      submittedStaff.push({ name: allStaff[s], count: staffCounts[allStaff[s]] });
    } else {
      missingStaff.push(allStaff[s]);
    }
  }

  // ── Build and send email ─────────────────────────────────────────
  var html = buildEmail(prettyDate, submittedStaff, missingStaff, allStaff, categoryMap, totalTasks);
  MailApp.sendEmail({
    to: CONFIG.recipientEmail,
    subject: "Daily Task Summary \u2014 " + prettyDate,
    htmlBody: html
  });
}

// ── Fuzzy name matcher ─────────────────────────────────────────────
// Matches a name entered in the log to the canonical staff list.
// Priority: 1) exact full-name  2) first-name match  3) starts-with prefix
// Returns the canonical staff name, or null if no unique match found.
function matchToStaff(logName, allStaff) {
  var input = logName.trim().toLowerCase();
  if (!input) return null;

  // 1. Exact full-name match (case-insensitive)
  for (var i = 0; i < allStaff.length; i++) {
    if (allStaff[i].toLowerCase() === input) return allStaff[i];
  }

  // 2. First-name match — input matches the first word of a staff name
  var candidates = [];
  for (var i = 0; i < allStaff.length; i++) {
    var firstName = allStaff[i].split(/\s+/)[0].toLowerCase();
    if (firstName === input) candidates.push(allStaff[i]);
  }
  if (candidates.length === 1) return candidates[0];

  // 3. Starts-with prefix — staff full name starts with input
  //    e.g. "Jane D" matches "Jane Doe"
  candidates = [];
  for (var i = 0; i < allStaff.length; i++) {
    if (allStaff[i].toLowerCase().indexOf(input) === 0) candidates.push(allStaff[i]);
  }
  if (candidates.length === 1) return candidates[0];

  // 4. Last-name match — input matches the last word of a staff name
  candidates = [];
  for (var i = 0; i < allStaff.length; i++) {
    var parts = allStaff[i].split(/\s+/);
    var lastName = parts[parts.length - 1].toLowerCase();
    if (lastName === input) candidates.push(allStaff[i]);
  }
  if (candidates.length === 1) return candidates[0];

  // No unique match — return null
  return null;
}

// ── Email builder ──────────────────────────────────────────────────
function buildEmail(prettyDate, submittedStaff, missingStaff, allStaff, categoryMap, totalTasks) {
  var html = [];

  html.push('<div style="font-family:Arial,Helvetica,sans-serif;max-width:600px;margin:0 auto;color:#1A1A1A;">');
  html.push('<h2 style="color:#1056DB;border-bottom:2px solid #1056DB;padding-bottom:8px;">');
  html.push('Daily Task Summary &mdash; ' + prettyDate + '</h2>');

  // ── Submitted staff ──────────────────────────────────────────────
  html.push('<h3 style="margin-top:24px;">Staff Who Submitted (' + submittedStaff.length + ')</h3>');
  if (submittedStaff.length === 0) {
    html.push('<p style="color:#888;">No submissions today.</p>');
  } else {
    html.push('<ul style="padding-left:20px;">');
    for (var i = 0; i < submittedStaff.length; i++) {
      html.push('<li>' + esc(submittedStaff[i].name) + ' &mdash; ' +
                submittedStaff[i].count + ' task' +
                (submittedStaff[i].count !== 1 ? 's' : '') + '</li>');
    }
    html.push('</ul>');
  }

  // ── Missing staff ────────────────────────────────────────────────
  html.push('<h3 style="margin-top:24px;color:#C0392B;">Staff Who Did Not Submit (' + missingStaff.length + ')</h3>');
  if (missingStaff.length === 0) {
    html.push('<p style="color:#27AE60;">Everyone submitted today!</p>');
  } else {
    html.push('<ul style="padding-left:20px;">');
    for (var i = 0; i < missingStaff.length; i++) {
      html.push('<li>' + esc(missingStaff[i]) + '</li>');
    }
    html.push('</ul>');
  }

  // ── Operations summary ──────────────────────────────────────────
  var categories = Object.keys(categoryMap).sort();
  html.push('<h3 style="margin-top:24px;">Today\u2019s Operations Summary</h3>');
  if (categories.length === 0) {
    html.push('<p style="color:#888;">No tasks recorded today.</p>');
  } else {
    for (var c = 0; c < categories.length; c++) {
      var cat = categories[c];
      html.push('<p style="margin:12px 0 4px;font-weight:bold;">' + esc(cat) + '</p>');
      html.push('<ul style="padding-left:20px;margin-top:0;">');
      var items = categoryMap[cat];
      var itemNames = Object.keys(items).sort();
      for (var j = 0; j < itemNames.length; j++) {
        html.push('<li>' + esc(itemNames[j]) + ' &times; ' + items[itemNames[j]] + '</li>');
      }
      html.push('</ul>');
    }
  }

  // ── Footer ───────────────────────────────────────────────────────
  html.push('<hr style="margin-top:24px;border:none;border-top:1px solid #ddd;">');
  html.push('<p style="font-size:12px;color:#888;">');
  html.push('Total: ' + totalTasks + ' task' + (totalTasks !== 1 ? 's' : '') +
            ' across ' + submittedStaff.length + ' of ' + allStaff.length + ' staff');
  html.push('<br>Sent automatically at 5 PM from Capricorn Drapery Task Log</p>');
  html.push('</div>');

  return html.join("\n");
}

// ── HTML-escape helper ─────────────────────────────────────────────
function esc(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Trigger installer (run once) ───────────────────────────────────
function installTrigger() {
  // Remove any existing daily-summary triggers to avoid duplicates
  var triggers = ScriptApp.getProjectTriggers();
  for (var i = 0; i < triggers.length; i++) {
    if (triggers[i].getHandlerFunction() === "sendDailySummary") {
      ScriptApp.deleteTrigger(triggers[i]);
    }
  }

  ScriptApp.newTrigger("sendDailySummary")
    .timeBased()
    .everyDays(1)
    .atHour(CONFIG.triggerHour)
    .inTimezone(CONFIG.timezone)
    .create();

  Logger.log("Trigger installed: sendDailySummary will run daily at " +
             CONFIG.triggerHour + ":00 " + CONFIG.timezone);
}
