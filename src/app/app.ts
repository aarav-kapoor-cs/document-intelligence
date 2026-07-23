import { Component, ChangeDetectorRef } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';

// Address of the Python (FastAPI) backend.
const backendUrl = 'http://localhost:8001';

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
  model = 'prebuilt-layout'; // which model is chosen

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
        this.errorMessage =
          'Could not reach the backend. Make sure it is running (cd backend-python, then uvicorn main:app --port 8001).';
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
      },
      error: (err) => {
        this.exportLoading = false;
        this.exportError =
          err.status === 0
            ? 'Could not reach the backend. Make sure it is running (cd backend-python, then uvicorn main:app --port 8001).'
            : 'The export failed - check the dates and try again.';
        this.cd.detectChanges();
      },
    });
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
