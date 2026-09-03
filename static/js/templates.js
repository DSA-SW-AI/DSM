
const documentTemplates = {

  // ── MEMORANDUM ──────────────────────────────────────────────
  memorandum: {
    type: 'structured',          // tells add_document.html to use the split approach

    defaultFields: {
      to: 'CDSA',
      from: 'DCS',
      ref: '',                  // will be auto-filled from reference_number field
      date: (() => {
        const d = new Date();
        return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: '2-digit' });
      })()
    },

    // Builds the locked A4 header rendered ABOVE the Quill editor
    buildHeader(fields = {}) {
      const to = fields.to || 'CDSA';
      const from = fields.from || 'DCS';
      const ref = fields.ref || '';
      const date = fields.date || '';
      return `
  <div class="memo-a4-header">

    

    <div class="memo-org-name">Defence Space Administration</div>

    <div class="memo-logo-wrap">
      <img src="/static/images/dsa2.png" alt="DSA Crest" />
    </div>

    <div class="memo-title-band">MEMORANDUM</div>

  <div class="memo-address-grid">
    <div class="memo-addr-cell">
      <div class="memo-addr-row">
        <span class="memo-addr-label">To:</span>
        <span class="memo-live-field" id="memo-live-to">${to}</span>
      </div>
      <div class="memo-addr-row">
        <span class="memo-addr-label">Ref:</span>
        <span class="memo-live-field" id="memo-live-ref">${ref}</span>
      </div>
    </div>
    <div class="memo-addr-divider"></div>
    <div class="memo-addr-cell">
      <div class="memo-addr-row">
        <span class="memo-addr-label">From:</span>
        <span class="memo-live-field" id="memo-live-from">${from}</span>
      </div>
      <div class="memo-addr-row">
        <span class="memo-addr-label">Date:</span>
        <span class="memo-live-field" id="memo-live-date">${date}</span>
      </div>
    </div>
  </div>



</div>`;
    },

    // What gets pre-loaded into the Quill body editor
    bodyPlaceholder: `<p><br></p>`
  },


  // ── LOOSE MINUTE ────────────────────────────────────────────
  loose_minute: {
    type: 'structured_loose_minute',

    buildBody(fields = {}) {
      const precedence = fields.precedence || 'ROUTINE';
      const ref = fields.ref || '';

      return `
<div class="memo-a4-header">
      

  <div style="text-align:right; margin-bottom:20px;">
    <h2 id="loose-minute-precedence">${precedence.toUpperCase()}</h2>
  </div>

  <p><strong>Ref:</strong> <span id="loose-minute-ref">${ref}</span></p>

  <p><br></p>


  <p><br></p>


  <p class="ql-align-right" style="width: 100px;">[SIGNATURE]</p>
  <strong><p class="ql-align-right"><span id="loose-minute-name">${fields.name || 'Name'}</span></p></strong>
  <p class="ql-align-right"><span id="loose-minute-rank">${fields.rank || 'Rank'}</span></p>
  <p class="ql-align-right"><span id="loose-minute-appt">${fields.appt || 'for Office'}</span></p>
  <p><br></p>



</div>`;
    },

    bodyPlaceholder: `
<p><br></p>
`
  },


  // ── MEMORANDUM ──────────────────────────────────────────────
  draft: {
    type: 'structured_draft',

    bodyHTML: `
          <div class="memo-a4-header">


            <div class="memo-title-band">DRAFT</div>



          </div>`



  },




  // ── OFFICIAL LETTER ────────────────────────────────────────
  letter: {
    type: 'quill',
    bodyHTML: `
<p class="ql-align-right">Defence Space Administration,</p>
<p class="ql-align-right">Abuja, Nigeria.</p>
<p class="ql-align-right"><br></p>
<p class="ql-align-right"><strong>Date:</strong>&nbsp;_____________________</p>
<p><br></p>
<p>The Addressee,</p>
<p>Title / Organisation,</p>
<p>Address Line 1,</p>
<p>Address Line 2.</p>
<p><br></p>
<p><strong>Dear Sir/Ma,</strong></p>
<p><br></p>
<p><strong><u>SUBJECT: _____________________</u></strong></p>
<p><br></p>
<p>1. &nbsp;I write to bring to your attention the above subject matter...</p>
<p><br></p>
<p>2. &nbsp;Continue paragraphs as needed.</p>
<p><br></p>
<p><br></p>
<p>Yours faithfully,</p>
<p><br></p>
<p><br></p>
<p>____________________________</p>
<p><strong>Name</strong></p>
<p>Rank / GL</p>
<p>Appointment</p>
<p>For: Director / Commander</p>
`
  },


  // ── CIRCULAR ────────────────────────────────────────────────
  circular: {
    type: 'quill',
    bodyHTML: `
<p class="ql-align-center"><strong>DEFENCE SPACE ADMINISTRATION</strong></p>
<p class="ql-align-center">CIRCULAR</p>
<p><br></p>
<p><strong>Ref:</strong>&nbsp;_____________________</p>
<p><strong>Date:</strong>&nbsp;_____________________</p>
<p><br></p>
<p><strong>TO ALL DIRECTORATES / UNITS</strong></p>
<p><br></p>
<p><strong><u>SUBJECT: _____________________</u></strong></p>
<p><br></p>
<p>1. &nbsp;This is to inform all concerned that...</p>
<p><br></p>
<p>2. &nbsp;Continue paragraphs as needed.</p>
<p><br></p>
<p><br></p>
<p>____________________________</p>
<p><strong>Name</strong></p>
<p>Rank / GL</p>
<p>Appointment</p>
<p>For: Director / Commander</p>
`
  }

};