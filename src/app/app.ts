import { Component, ChangeDetectorRef } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';

// Address of the Python (FastAPI) backend.
const backendUrl = 'http://localhost:8001';

// HTTP status 0 means the request never arrived, which is almost always the
// backend not running rather than anything wrong with the request. Every screen
// says so the same way, so the sentence lives here once.
const backendDownMessage =
  'Could not reach the backend. Make sure it is running (cd backend-python, then uvicorn main:app --port 8001).';

@Component({
  selector: 'app-root',
  imports: [FormsModule],
  templateUrl: './app.html',
})
export class App {
  service = ''; // which service is chosen in the dropdown

  // The Document Intelligence models you can choose from.
  models = [
    { id: 'prebuilt-layout', label: 'Layout (text + tables + key-value pairs)' },
    { id: 'prebuilt-invoice', label: 'Invoice' },
    { id: 'prebuilt-receipt', label: 'Receipt' },
    { id: 'prebuilt-idDocument', label: 'ID Document' },
  ];
  model = 'prebuilt-invoice'; // which model is chosen (same default as the Excel export)

  // The document types the Excel report can be built for.
  exportTypes = [
    { id: 'prebuilt-invoice', label: 'Invoice' },
    { id: 'prebuilt-receipt', label: 'Receipt' },
    { id: 'prebuilt-layout', label: 'General layout' },
    { id: 'prebuilt-idDocument', label: 'ID Document' },
  ];
  exportFromDate = '';
  exportToDate = '';
  exportDocType = 'prebuilt-invoice';
  exportLoading = false;
  exportMessage = '';
  exportError = '';
  reindexMessage = ''; // reported separately: the download can succeed while this fails

  // ---- AI Search (the chat box) ----
  // The full explorer - chunks, the four-way retrieval comparison, the agent
  // trace - is served by the backend itself and opens in its own tab.
  explorerUrl = backendUrl + '/search';

  // 'rag'   -> retrieve chunks, then have the model write a grounded answer.
  // 'agent' -> let Semantic Kernel pick a tool (exact SQL count, fetch one
  //            document, check whether a field even applies) and show which.
  searchMode: 'rag' | 'agent' = 'rag';

  // Which chunks RAG is allowed to read. This is not decoration: asked over
  // document chunks, "which fields are extracting badly" answers with optional
  // fields no invoice ever had, while field chunks give the real gaps.
  searchGrain = 'field';

  searchQuestion = 'which fields are extracting badly';
  searchLoading = false;
  searchError = '';
  searchAnswer = '';
  searchHits: { id: string; entity: string; sheet: string; content: string }[] = [];
  searchTrace: { tool: string; arguments: any }[] = [];
  indexStatus = ''; // e.g. "84 chunks from Audit_04082026.xlsx"

  // ---- Anomaly detector ----
  // Scores each saved invoice 0-100. The rules need no training and no Azure, so
  // this screen works before the Azure ML model exists; anomalyMethod says which
  // of the two produced the numbers on screen.
  anomalyFromDate = '';
  anomalyToDate = '';
  anomalyDocType = 'prebuilt-invoice'; // content checks only mean anything for invoices
  anomalyLoading = false;
  anomalyError = '';
  anomalyStatus = '';
  anomalyMethod = '';
  anomalyNotes: string[] = [];
  anomalyResults: {
    documentId: number | null;
    sourceFile: string;
    invoiceNumber: string;
    score: number;
    band: string;
    signals: { name: string; layer: string; detail: string; contribution: number }[];
  }[] = [];

  files: { name: string; base64: string }[] = []; // the files the user picked
  prompt = ''; // the prompt the user types
  results: {
    fileName: string;
    text: string;
    fields: { [name: string]: { value: any; confidence: number | null } };
    answer: string;
    error: string;
    tokens: { promptTokens: number; completionTokens: number; totalTokens: number } | null;
  }[] = [];
  loading = false;
  errorMessage = '';

  constructor(
    private http: HttpClient,
    private cd: ChangeDetectorRef,
  ) {}

  // Step 2: read each chosen file and keep it as base64 text (so we can send it as JSON).
  async chooseFiles(event: any) {
    const chosen = event.target.files;
    this.files = [];
    this.results = [];

    for (let i = 0; i < chosen.length; i++) {
      const file = chosen[i];
      const base64 = await this.toBase64(file);
      this.files.push({ name: file.name, base64: base64 });
    }
    this.cd.detectChanges();
  }

  // Turn a file into a base64 string (without the "data:...;base64," prefix at the front).
  toBase64(file: File): Promise<string> {
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = reader.result as string;
        resolve(result.split(',')[1]);
      };
      reader.readAsDataURL(file);
    });
  }

  // Step 3: send the model, prompt, and files to the backend. It reads the files with the
  // chosen Document Intelligence model, checks the document type, asks the AI, and saves
  // the result. Each extracted field arrives as { value, confidence } and is shown below.
  getAnswer() {
    this.loading = true;
    this.errorMessage = '';
    this.results = [];

    const body = { model: this.model, prompt: this.prompt, files: this.files };
    this.http.post(backendUrl + '/api/analyze', body).subscribe({
      next: (res: any) => {
        // The FastAPI response uses snake_case names, so map them to our shape.
        this.results = (res.results || []).map((r: any) => ({
          fileName: r.file_name,
          text: r.text,
          fields: r.fields || {},
          answer: r.answer,
          error: r.error,
          tokens: r.tokens
            ? {
                promptTokens: r.tokens.prompt_tokens,
                completionTokens: r.tokens.completion_tokens,
                totalTokens: r.tokens.total_tokens,
              }
            : null,
        }));
        this.loading = false;
        this.cd.detectChanges();
      },
      error: () => {
        this.loading = false;
        this.errorMessage = backendDownMessage;
        this.cd.detectChanges();
      },
    });
  }

  // Ask the backend to build the audit Excel for the chosen dates and type,
  // then hand the file to the browser as a download.
  downloadExcel() {
    this.exportMessage = '';
    this.exportError = '';

    if (!this.exportFromDate || !this.exportToDate) {
      this.exportError = 'Please pick both dates.';
      return;
    }
    if (this.exportFromDate > this.exportToDate) {
      this.exportError = 'The from date must not be after the to date.';
      return;
    }

    this.exportLoading = true;
    this.reindexMessage = '';
    const body = {
      from_date: this.exportFromDate,
      to_date: this.exportToDate,
      doc_type: this.exportDocType,
    };
    // responseType 'blob' = "give me the raw file bytes, not JSON".
    this.http.post(backendUrl + '/api/export', body, { responseType: 'blob' }).subscribe({
      next: (blob) => {
        // Turn the bytes into a temporary URL and click an invisible link to
        // it - that is how a web page starts a download.
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        const today = new Date();
        const stamp =
          String(today.getDate()).padStart(2, '0') +
          String(today.getMonth() + 1).padStart(2, '0') +
          today.getFullYear();
        link.download = 'Audit_' + stamp + '.xlsx';
        link.click();
        URL.revokeObjectURL(url);
        this.exportMessage = 'This is your Excel sheet downloaded.';
        this.exportLoading = false;
        this.cd.detectChanges();
        // The file is already on disk, so the search index can now catch up in
        // the background. Kept as its own call rather than hidden inside
        // /api/export: the download should not wait on 84 embedding calls.
        this.refreshSearchIndex(body);
      },
      error: (err) => {
        this.exportLoading = false;
        this.exportError =
          err.status === 0
            ? backendDownMessage
            : 'The export failed - check the dates and try again.';
        this.cd.detectChanges();
      },
    });
  }

  // Re-chunk the freshly exported workbook and upload it to Azure AI Search, so
  // asking a question reflects the documents just analysed. A failure here is
  // reported on its own line - the Excel download itself already succeeded.
  refreshSearchIndex(body: { from_date: string; to_date: string; doc_type: string }) {
    this.reindexMessage = 'Updating the search index...';
    this.cd.detectChanges();
    this.http.post<any>(backendUrl + '/api/search/reindex', body).subscribe({
      next: (r) => {
        this.reindexMessage =
          'Search index updated - ' + r.chunks + ' chunks (' + r.documents +
          ' documents, ' + r.fields + ' fields) from ' + r.workbook + '.';
        this.cd.detectChanges();
      },
      error: (err) => {
        this.reindexMessage =
          'The Excel downloaded, but the search index was not updated: ' +
          (err.error?.detail || 'the backend could not be reached') + '.';
        this.cd.detectChanges();
      },
    });
  }

  // ---- AI Search: ask the audit a question ----

  // Two ways to answer, both already served by the backend. RAG retrieves chunks
  // and has the model write a grounded answer with [chunk-id] citations. The
  // agent instead picks a tool - and counting is the clearest reason it exists,
  // because search returns matching chunks, not a total.
  askSearch() {
    if (!this.searchQuestion.trim()) {
      this.searchError = 'Please type a question.';
      return;
    }
    this.searchLoading = true;
    this.searchError = '';
    this.searchAnswer = '';
    this.searchHits = [];
    this.searchTrace = [];

    const url = this.searchMode === 'agent' ? '/api/search/agent' : '/api/search/ask';
    // 'top' is deliberately not sent: the backend fills it with RAG_CONTEXT when
    // an answer is asked for, the same rule the command line uses.
    const body =
      this.searchMode === 'agent'
        ? { question: this.searchQuestion }
        : { question: this.searchQuestion, grain: this.searchGrain, answer: true };

    this.http.post<any>(backendUrl + url, body).subscribe({
      next: (r) => {
        this.searchAnswer = r.answer || '(no answer came back)';
        this.searchHits = r.hits || [];
        // The agent's trace holds a 'call' and a 'result' entry per tool; only
        // the calls are worth showing, because they are the routing decision.
        this.searchTrace = (r.trace || []).filter((s: any) => s.type === 'call');
        this.searchLoading = false;
        this.cd.detectChanges();
      },
      error: (err) => {
        this.searchLoading = false;
        this.searchError =
          err.status === 0
            ? backendDownMessage
            : err.error?.detail || 'The search failed - try again.';
        this.cd.detectChanges();
      },
    });
  }

  // Opening the AI Search screen reads the index status once, so the page can
  // say straight away whether anything is indexed at all.
  onServiceChange() {
    if (this.service === 'search') {
      this.loadIndexStatus();
    }
    if (this.service === 'anomaly') {
      this.loadAnomalyStatus();
    }
  }

  // ---- Anomaly detector ----

  // Score every saved invoice in the window. The backend applies the rules
  // always and the trained model only if one is installed, so this returns
  // useful findings whether or not the Azure ML half has been run.
  scanAnomalies() {
    if (!this.anomalyFromDate || !this.anomalyToDate) {
      this.anomalyError = 'Please choose both dates.';
      return;
    }
    if (this.anomalyFromDate > this.anomalyToDate) {
      this.anomalyError = 'The from date must not be after the to date.';
      return;
    }

    this.anomalyLoading = true;
    this.anomalyError = '';
    this.anomalyResults = [];
    this.anomalyNotes = [];
    const body = {
      from_date: this.anomalyFromDate,
      to_date: this.anomalyToDate,
      doc_type: this.anomalyDocType,
    };

    this.http.post<any>(backendUrl + '/api/anomaly/scan', body).subscribe({
      next: (r) => {
        this.anomalyResults = (r.results || []).map((row: any) => ({
          documentId: row.document_id,
          sourceFile: row.source_file,
          invoiceNumber: row.invoice_number,
          score: row.score,
          band: row.band,
          // Model contributions are per-feature numbers, useful for debugging but
          // not readable as findings, so the table shows the checks that fired.
          signals: (row.signals || []).filter((s: any) => s.layer !== 'model'),
        }));
        this.anomalyMethod = r.method;
        this.anomalyNotes = r.notes || [];
        this.anomalyLoading = false;
        this.cd.detectChanges();
      },
      error: (err) => {
        this.anomalyLoading = false;
        this.anomalyError =
          err.status === 0
            ? backendDownMessage
            : err.error?.detail || 'The scan failed - check the dates and try again.';
        this.cd.detectChanges();
      },
    });
  }

  // Whether a trained model is installed, so the page can say up front which
  // kind of number the scores are before anyone reads one.
  loadAnomalyStatus() {
    this.anomalyStatus = 'Checking the anomaly detector...';
    this.cd.detectChanges();
    this.http.get<any>(backendUrl + '/api/anomaly/status').subscribe({
      next: (r) => {
        this.anomalyStatus = r.model_installed
          ? 'Scoring with a logistic regression trained in Azure ML (run ' +
            r.model_version + '), plus ' + r.rules + ' rules.'
          : r.rules + ' deterministic rules, no trained model installed yet.';
        if (r.notes?.length) {
          this.anomalyStatus += ' (' + r.notes[0] + ')';
        }
        this.cd.detectChanges();
      },
      error: (err) => {
        this.anomalyStatus =
          err.status === 0
            ? backendDownMessage
            : 'The anomaly detector status could not be read: ' +
              (err.error?.detail || 'HTTP ' + err.status) + '.';
        this.cd.detectChanges();
      },
    });
  }

  // Excel's Good / Neutral / Bad colours, the same three the workbook uses to
  // grade validity, so a red row means the same thing in both places.
  bandClass(band: string): string {
    return 'band-' + band.toLowerCase();
  }

  // How many chunks are indexed and which workbook they came from, so a stale
  // index is visible rather than something you discover from a wrong answer.
  // This takes a few seconds: the backend re-reads the workbook and makes a real
  // embedding call to check that half is configured, so say so rather than
  // leaving the line blank and looking frozen.
  loadIndexStatus() {
    this.indexStatus = 'Checking the search index...';
    this.cd.detectChanges();
    this.http.get<any>(backendUrl + '/api/search/status').subscribe({
      next: (r) => {
        this.indexStatus = r.chunks
          ? r.chunks + ' chunks indexed, from ' + (r.workbook || 'an unknown workbook')
          : 'No chunks indexed yet - export the Excel report to build the index.';
        // notes carry the exact reason a piece is unconfigured, e.g. an
        // embedding model that exists in the region but is not deployed.
        if (r.notes?.length) {
          this.indexStatus += ' (' + r.notes[0] + ')';
        }
        this.cd.detectChanges();
      },
      error: (err) => {
        // status 0 means the request never arrived - almost always the backend
        // not running. Saying that outright beats "could not be read", which
        // sends you looking at Azure when the problem is a stopped terminal.
        this.indexStatus =
          err.status === 0
            ? backendDownMessage
            : 'The search index status could not be read: ' +
              (err.error?.detail || 'HTTP ' + err.status) + '.';
        this.cd.detectChanges();
      },
    });
  }

  // Show the arguments a tool was called with, e.g. "field=Vendor GSTIN".
  traceArgs(args: any): string {
    return Object.keys(args || {})
      .map((k) => k + '=' + JSON.stringify(args[k]))
      .join(', ');
  }

  // ---- Helpers for showing the extracted fields ----

  // Turn the fields object into a list the template can loop over.
  fieldList(fields: { [name: string]: { value: any; confidence: number | null } }) {
    return Object.keys(fields).map((name) => ({ name, ...fields[name] }));
  }

  // Format a 0-1 confidence as a percentage, e.g. 0.94 -> "94%".
  confidencePercent(confidence: number | null): string {
    return confidence == null ? '—' : Math.round(confidence * 100) + '%';
  }

  isLowConfidence(confidence: number | null): boolean {
    return confidence != null && confidence < 0.8;
  }

  // Every extracted value arrives wrapped as { value, confidence } - at the
  // top level and at every nested level (line items and their cells). This
  // returns the value inside a wrapper, and anything else unchanged.
  unwrap(node: any): any {
    if (node !== null && typeof node === 'object' && 'value' in node && 'confidence' in node) {
      return node.value;
    }
    return node;
  }

  // True for a list of objects, e.g. an invoice's line items.
  isItemList(value: any): boolean {
    return (
      Array.isArray(value) &&
      value.length > 0 &&
      value.every((item) => {
        const inner = this.unwrap(item);
        return inner !== null && typeof inner === 'object' && !Array.isArray(inner);
      })
    );
  }

  // Column names for a line-item table: every key used by any of the items.
  itemColumns(items: any[]): string[] {
    const columns: string[] = [];
    for (const item of items) {
      for (const key of Object.keys(this.unwrap(item) || {})) {
        if (!columns.includes(key)) columns.push(key);
      }
    }
    return columns;
  }

  // One line-item cell as its { value, confidence } wrapper, or null when the
  // item has no such column (e.g. Azure found no Description for it).
  cell(item: any, col: string): { value: any; confidence: number | null } | null {
    return this.unwrap(item)?.[col] ?? null;
  }

  // Plain display text for a value; "—" when there is nothing to show.
  fieldText(value: any): string {
    value = this.unwrap(value);
    if (value === null || value === undefined || value === '') return '—';
    if (Array.isArray(value)) return value.map((v) => this.fieldText(v)).join(', ');
    if (typeof value === 'object') {
      return Object.entries(value)
        .map(([key, v]) => key + ': ' + this.fieldText(v))
        .join(', ');
    }
    return String(value);
  }
}
