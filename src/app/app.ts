import { Component, ChangeDetectorRef } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';

// Address of the .NET backend. Change this if your backend runs on a different port.
const backendUrl = 'http://localhost:5011';

@Component({
  selector: 'app-root',
  imports: [FormsModule],
  templateUrl: './app.html',
})
export class App {
  service = ''; // which service is chosen in the dropdown
  files: { name: string; base64: string }[] = []; // the files the user picked
  prompt = ''; // the prompt the user types
  results: { fileName: string; answer: string }[] = []; // answers from the backend
  loading = false;
  errorMessage = '';

  constructor(
    private http: HttpClient,
    private cd: ChangeDetectorRef,
  ) {}

  // Step 1: read each chosen file and keep it as base64 text (so we can send it as JSON).
  // The backend reads the text out of the file with Azure OCR.
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

  // Step 2: send the prompt and files to the backend. The backend reads the text with
  // Azure OCR, asks the AI, saves it to the database, and returns the answers.
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
