# Detailed Implementation Instructions for Zolon TGIF App – Phase 2

This document provides comprehensive, step‑by‑step instructions for implementing the second round of changes requested by my supervisor. These instructions are written for an AI assistant (like VS Code Copilot) to understand exactly what to modify, where, and how.

---

## Overview of Changes

We need to:

1. **Rep Portal** – Move the TGIF date display to the top of the card (above all input fields).
2. **Admin Dashboard** – Change the “View Submission Report” to open as a **modal/pop‑up** overlay instead of inline below the feed. The modal should have a close (X) button and a “Check Another Batch” button that simply hides the modal without resetting the page.
3. **Rep Portal – Batch Photo Capture** – Implement a custom file queue that:
   - Has **two separate buttons**: “Take Photo” (uses camera) and “Choose from Gallery” (opens gallery).
   - Both buttons **append** new photos to an existing list (do not replace).
   - Shows a preview queue with thumbnails and a “Remove” button for each file.
   - The submit button is enabled only when at least one photo is in the queue (along with rep name and pharmacy name).

---

## 1. Rep Portal – Move TGIF Date to Top of Card

### File: `templates/submit.html`

#### Current Structure (simplified):
```html
<div class="card">
  <form id="rep-form">
    <div class="form-group"><!-- Rep Name --></div>
    <div class="form-group"><!-- TGIF Date (read-only) --></div>
    <div class="form-group"><!-- Pharmacy Name --></div>
    <div class="form-group"><!-- Photo Upload --></div>
    <button type="submit">Submit</button>
  </form>
</div>
```

#### Required Change:
Move the TGIF date `<div class="form-group">` **above** the Rep Name field, so it’s the first thing inside the card.

**New structure:**
```html
<div class="card">
  <form id="rep-form">
    <div class="form-group"><!-- TGIF Date (read-only) – now first --></div>
    <div class="form-group"><!-- Rep Name --></div>
    <div class="form-group"><!-- Pharmacy Name --></div>
    <div class="form-group"><!-- Photo Upload (will be replaced with new queue) --></div>
    <button type="submit">Submit</button>
  </form>
</div>
```

**Note:** The TGIF date display already exists (we added it in Phase 1). We’re just changing its position in the DOM. No JavaScript changes needed for this part.

---

## 2. Admin Dashboard – Results as Modal/Pop-up

### File: `templates/index.html`

#### Current Behavior:
- The results section (`#section-results`) is a full card that appears **below** the feed.
- Clicking “View Submission Report” makes this card visible.
- Clicking “Check Another Batch” resets the entire app (clears session, hides results, scrolls to top).

#### Required Changes:

### 2.1 Create a Modal Overlay

We need to create a modal that appears **on top** of the page content. This modal will contain the results card (or we can move the entire results card inside the modal).

**Option A (recommended):** Keep the results card where it is in the HTML but wrap it in a modal container with `display: none;`. When opened, the modal covers the screen with a semi‑transparent backdrop.

**Option B:** Move the results HTML into a new modal structure.

I recommend **Option A** – it requires less DOM restructuring.

**Implementation steps:**

1. **Add a modal wrapper** around the existing `#section-results`:
   ```html
   <div id="results-modal" class="modal-overlay" style="display:none;">
     <div class="modal-content">
       <button class="modal-close" onclick="closeResultsModal()">✕</button>
       <section id="section-results" class="card">
         <!-- Existing results card content -->
       </section>
       <div class="modal-footer">
         <button class="btn btn-outline" onclick="closeResultsModal()">↩ Check Another Batch</button>
       </div>
     </div>
   </div>
   ```

2. **Add CSS** for the modal:
   - `.modal-overlay`: fixed position, full screen, dark semi‑transparent background, `z-index: 1000`, flex centering.
   - `.modal-content`: background white, max‑width (e.g., 900px), max‑height 90vh, overflow auto, border‑radius, box‑shadow, relative positioning.
   - `.modal-close`: absolute top‑right, large clickable X button.

3. **Modify JavaScript:**
   - Update `loadResults()` – instead of `showSection('results')`, it should open the modal: `document.getElementById('results-modal').style.display = 'flex';`.
   - Add `closeResultsModal()` function that hides the modal and does **not** reset the page.
   - Remove `resetApp()` call from the “Check Another Batch” button (we’ll just call `closeResultsModal()`).
   - The existing `resetApp()` function can be kept but no longer called from the results modal.

4. **Remove the old inline results display logic:**
   - In `renderResults()`, remove `showSection('results')`.
   - Instead, just open the modal after rendering results.

### 2.2 Adjust the Feed’s “View Submission Report” Button

No change needed – it already calls `viewRepSession(sessionId)`, which calls `loadResults()`. That will now open the modal.

### 2.3 Ensure “Check Another Batch” Does Not Reset

- The current `resetApp()` clears `selectedFiles`, hides sections, scrolls to top – we want to avoid that when closing the modal.
- We’ll create a new `closeResultsModal()` that:
  - Hides the modal.
  - Does **not** clear the `currentSession` (so if the user re‑opens, it still works).
  - Does **not** scroll or reset anything else.

---

## 3. Rep Portal – Batch Photo Capture with File Queue

### File: `templates/submit.html`

#### Current Behavior:
- Single file input that replaces selection each time.
- No preview or removal.

#### Required Changes:

### 3.1 Replace the Existing Upload Section

Remove the current file input and its surrounding HTML. Replace with:

1. **Two buttons:**
   - “📷 Take Photo” – uses `capture="environment"` attribute to open camera directly.
   - “🖼️ Choose from Gallery” – opens the file picker without capture.

   Both buttons should trigger a hidden file input, but with different `capture` attributes.

   *Implementation trick:* Since a single `<input>` can’t have two different `capture` values, we use **two hidden inputs**:
   - `<input type="file" id="camera-input" accept="image/*" capture="environment" multiple>`
   - `<input type="file" id="gallery-input" accept="image/*" multiple>`

   Each button clicks its corresponding input.

2. **File queue display:**
   - Show a list/grid of selected files with thumbnails.
   - Each item shows filename and a “Remove” (✕) button.
   - Show total count and total size.

### 3.2 JavaScript Logic

We need to manage a persistent array of files (similar to the admin dashboard but simplified).

**State:**
```javascript
let selectedFiles = []; // Array of File objects
```

**Functions to implement:**

- `addFiles(newFiles)` – appends new files to `selectedFiles` (deduplicate by name+size to avoid duplicates).
- `removeFile(index)` – removes a file from the array by index.
- `renderFileQueue()` – renders the queue UI, updates the submit button state.
- `updateSubmitButton()` – checks if rep name, pharmacy name, and `selectedFiles.length > 0` are all truthy; enables/disables submit button.

**Event listeners:**

- Camera button → click → `document.getElementById('camera-input').click()`.
- Gallery button → click → `document.getElementById('gallery-input').click()`.
- On `change` of either input, call `addFiles(input.files)` and then clear the input value (so the same file can be selected again later if needed).
- Input events on rep name and pharmacy name → call `updateSubmitButton()`.

**Submit handler modification:**
- Instead of using `document.getElementById('photo-input').files`, use `selectedFiles` array.
- Append each file in `selectedFiles` to `FormData`.
- **Important:** The backend expects field name `images` for each file – keep that.

### 3.3 UI/UX Considerations

- The queue should be scrollable if many files are added.
- Show a placeholder when no files are selected.
- Use the same styling as the admin dashboard’s file queue (reuse CSS classes).
- On mobile, the queue items should be touch‑friendly (large enough tap targets for remove buttons).

### 3.4 Prevent Duplicate Files

- Use a `Set` with key `filename + '|' + file.size` to avoid adding the exact same file twice.
- If a user tries to add a duplicate, show a small notification.

### 3.5 Maintain Submit Button State

- Initially disabled.
- Enabled when all three conditions are met.
- Disabled if any condition becomes false (e.g., user removes all files, or clears a text field).

---

## 4. Additional Notes for Copilot

### 4.1 Styling Consistency

- Use existing CSS variables for colors, spacing, border‑radius.
- For the modal, mimic the lightbox styling but with a card inside.
- For the queue, reuse `.file-queue-wrap`, `.file-item`, etc., from the admin dashboard (or define similar styles in `submit.html`).

### 4.2 Accessibility

- Ensure buttons have proper `aria-label` attributes.
- The modal should trap focus when open (optional but nice – not required for this phase).

### 4.3 Testing Checklist

After implementation, test the following:

- [ ] Rep portal: TGIF date appears at the top of the card.
- [ ] Rep portal: “Take Photo” opens camera and appends photo.
- [ ] Rep portal: “Choose from Gallery” opens gallery and appends photos.
- [ ] Rep portal: Queue shows thumbnails/previews.
- [ ] Rep portal: Remove button works and updates the queue.
- [ ] Rep portal: Submit button is disabled until all fields are filled and at least one photo is in the queue.
- [ ] Rep portal: Submission works and sends all files to backend.
- [ ] Admin dashboard: Click “View Submission Report” – modal opens.
- [ ] Admin dashboard: Modal appears above the feed.
- [ ] Admin dashboard: Click X or “Check Another Batch” – modal closes, feed remains visible and unchanged.
- [ ] Admin dashboard: Click “View Submission Report” again – modal re‑opens with the same results.

### 4.4 Backend Considerations

- The backend `/api/submit-rep-photos` already accepts multiple `images` files and handles them in a loop. No changes needed – it will work with the new queue because we’re still sending all files in one request.
- The `pharmacy_name` is already being saved (Phase 1). No additional backend work required.

### 4.5 Files to Modify

| File | Changes |
|------|---------|
| `templates/submit.html` | Move TGIF date to top; replace upload section with two buttons + file queue; add JavaScript for file management and submit button control. |
| `templates/index.html` | Add modal wrapper around results; add modal CSS; modify `loadResults()` to open modal; add `closeResultsModal()`; remove `resetApp()` call from results. |
| `static/` (optional) | Add any additional CSS if needed (but we can embed in the templates). |

---

## 5. Implementation Order (Suggested)

1. **Rep Portal – Move TGIF Date** (quick fix).
2. **Rep Portal – File Queue Logic** – implement the JavaScript state management, functions, and event listeners.
3. **Rep Portal – UI Update** – replace the upload section with the new buttons and queue renderer.
4. **Admin Dashboard – Modal** – add modal wrapper, CSS, and JavaScript.
5. **Testing** – verify all flows end‑to‑end.

---

## 6. Summary of Key Behaviors

| Feature | Behavior |
|---------|----------|
| **Rep Portal – Date Position** | TGIF date appears at the very top of the form card. |
| **Rep Portal – Photo Capture** | Two buttons: “Take Photo” (camera) and “Choose from Gallery”. Both append files to a persistent queue. |
| **Rep Portal – File Queue** | Shows thumbnails (optional but recommended), filenames, and remove buttons. |
| **Rep Portal – Submit Button** | Enabled only when rep name, pharmacy name, and at least one file are provided. |
| **Admin Dashboard – Results Modal** | Clicking “View Submission Report” opens a modal overlay with the results. |
| **Admin Dashboard – Close Modal** | Clicking X or “Check Another Batch” closes the modal without resetting the page. |

---

These instructions should enable the AI assistant to implement the requested changes with clarity and precision. If any clarification is needed, the assistant can refer back to this document. Good luck!