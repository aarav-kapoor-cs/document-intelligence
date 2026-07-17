import { Component, ChangeDetectorRef } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';

// Address of the Python (FastAPI) backend. (The .NET backend is on 5011 if you switch back.)
const backendUrl = 'http://localhost:8000';

@Component({
  selector: 'app-root',
  imports: [FormsModule],
  templateUrl: './app.html',
})
export class App {
  service = ''; // which service is chosen in the dropdown

  // The Document Intelligence models you can choose from.
  models = [
    { id: 'prebuilt-read', label: 'Read (OCR text)' },
    { id: 'prebuilt-layout', label: 'Layout (text + tables + key-value pairs)' },
    { id: 'prebuilt-invoice', label: 'Invoice' },
    { id: 'prebuilt-receipt', label: 'Receipt' },
    { id: 'prebuilt-idDocument', label: 'ID Document' },
  ];
  model = 'prebuilt-layout'; // which model is chosen

  files: { name: string; base64: string }[] = []; // the files the user picked
  prompt = ''; // the prompt the user types
  results: {
    fileName: string;
    text: string;
    keyValues: { key: string; value: string; confidence: number }[];
    answer: string;
    tokens: { promptTokens: number; completionTokens: number; totalTokens: number } | null;
  }[] = [];
  loading = false;
  errorMessage = '';

  constructor(
    private http: HttpClient,
    private cd: ChangeDetectorRef,
  ) {}

  // Turn a confidence (0.0 - 1.0) into a whole-number percent for display.
  pct(confidence: number) {
    return Math.round(confidence * 100);
  }

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
  // chosen Document Intelligence model, asks the AI, saves it, and returns the results.
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
          keyValues: (r.key_values || []).map((kv: any) => ({
            key: kv.key,
            value: kv.value,
            confidence: kv.confidence,
          })),
          answer: r.answer,
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
          'Could not reach the backend. Make sure it is running (cd backend-python, then uvicorn main:app --port 8000).';
        this.cd.detectChanges();
      },
    });
  }
}
