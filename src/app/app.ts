import { Component, ChangeDetectorRef } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';
import * as pdfjsLib from 'pdfjs-dist';

// Tell pdf.js where its worker file is (angular.json copies it into the app).
pdfjsLib.GlobalWorkerOptions.workerSrc = 'pdf.worker.min.mjs';

// Address of the .NET backend. Change this if your backend runs on a different port.
const backendUrl = 'http://localhost:5011';

@Component({
  selector: 'app-root',
  imports: [FormsModule],
  templateUrl: './app.html',
})
export class App {
  service = ''; // which service is chosen in the dropdown
  files: { name: string; text: string }[] = []; // the PDFs the user picked
  prompt = ''; // the prompt the user types
  results: { fileName: string; answer: string }[] = []; // answers from the backend
  loading = false;
  errorMessage = '';

  constructor(
    private http: HttpClient,
    private cd: ChangeDetectorRef,
  ) {}

  // Step 1: read the text out of each chosen PDF.
  async chooseFiles(event: any) {
    const chosen = event.target.files;
    this.files = [];
    this.results = [];

    for (let i = 0; i < chosen.length; i++) {
      const file = chosen[i];
      const data = new Uint8Array(await file.arrayBuffer());
      const pdf = await pdfjsLib.getDocument({ data }).promise;
      let text = '';
      for (let p = 1; p <= pdf.numPages; p++) {
        const page = await pdf.getPage(p);
        const content = await page.getTextContent();
        const words = content.items.map((it: any) => it.str);
        text = text + words.join(' ') + ' ';
      }
      this.files.push({ name: file.name, text: text });
    }
    this.cd.detectChanges();
  }

  // Step 2: send the prompt and files to the backend. The backend saves them in the
  // database and returns the answers.
  getAnswer() {
    this.loading = true;
    this.errorMessage = '';
    this.results = [];

    const body = { prompt: this.prompt, files: this.files };
    this.http.post(backendUrl + '/api/analyze', body).subscribe({
      next: (res: any) => {
        this.results = res.results;
        this.loading = false;
        this.cd.detectChanges();
      },
      error: () => {
        this.loading = false;
        this.errorMessage = 'Could not reach the backend. Make sure it is running (cd backend, then dotnet run).';
        this.cd.detectChanges();
      },
    });
  }
}
