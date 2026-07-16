using System.Data.Common;
using Microsoft.Data.Sqlite;
using Microsoft.Data.SqlClient;

namespace DocIntelligenceApi;

// A small database helper that uses plain SQL: CREATE TABLE, INSERT, SELECT.
// By default it saves to a SQLite file (documents.db) on this computer.
// If "UseSqlServer" is true in appsettings.json, it uses SQL Server instead.
public class Database
{
    private readonly bool _useSqlServer;
    private readonly string _connectionString;

    public Database(IConfiguration config)
    {
        _useSqlServer = config.GetValue<bool>("UseSqlServer");
        _connectionString = _useSqlServer
            ? (config.GetConnectionString("SqlServer") ?? "")
            : "Data Source=documents.db";
    }

    // Open the right kind of database connection.
    private DbConnection OpenConnection()
    {
        DbConnection connection = _useSqlServer
            ? new SqlConnection(_connectionString)
            : new SqliteConnection(_connectionString);
        connection.Open();
        return connection;
    }

    // Make the table the first time. Does nothing if it already exists.
    public void EnsureTable()
    {
        using DbConnection connection = OpenConnection();
        DbCommand command = connection.CreateCommand();
        command.CommandText = _useSqlServer
            ? @"IF OBJECT_ID('Documents', 'U') IS NULL
                CREATE TABLE Documents (
                    Id INT IDENTITY(1,1) PRIMARY KEY,
                    FileName NVARCHAR(400),
                    Prompt NVARCHAR(MAX),
                    DocumentText NVARCHAR(MAX),
                    Answer NVARCHAR(MAX),
                    CreatedAt NVARCHAR(40)
                );"
            : @"CREATE TABLE IF NOT EXISTS Documents (
                    Id INTEGER PRIMARY KEY AUTOINCREMENT,
                    FileName TEXT,
                    Prompt TEXT,
                    DocumentText TEXT,
                    Answer TEXT,
                    CreatedAt TEXT
                );";
        command.ExecuteNonQuery();
    }

    // INSERT one row into the table.
    public void Save(string fileName, string prompt, string documentText, string answer)
    {
        using DbConnection connection = OpenConnection();
        DbCommand command = connection.CreateCommand();
        command.CommandText =
            "INSERT INTO Documents (FileName, Prompt, DocumentText, Answer, CreatedAt) " +
            "VALUES (@fileName, @prompt, @documentText, @answer, @createdAt)";
        AddParameter(command, "@fileName", fileName);
        AddParameter(command, "@prompt", prompt);
        AddParameter(command, "@documentText", documentText);
        AddParameter(command, "@answer", answer);
        AddParameter(command, "@createdAt", DateTime.UtcNow.ToString("yyyy-MM-dd HH:mm:ss"));
        command.ExecuteNonQuery();
    }

    // SELECT every row, newest first.
    public List<DocumentRecord> GetAll()
    {
        List<DocumentRecord> records = new();
        using DbConnection connection = OpenConnection();
        DbCommand command = connection.CreateCommand();
        command.CommandText =
            "SELECT Id, FileName, Prompt, DocumentText, Answer, CreatedAt FROM Documents ORDER BY Id DESC";
        using DbDataReader reader = command.ExecuteReader();
        while (reader.Read())
        {
            records.Add(new DocumentRecord
            {
                Id = reader.GetInt32(0),
                FileName = reader.GetString(1),
                Prompt = reader.GetString(2),
                DocumentText = reader.GetString(3),
                Answer = reader.GetString(4),
                CreatedAt = reader.GetString(5),
            });
        }
        return records;
    }

    // Small helper so we do not repeat the parameter code.
    private static void AddParameter(DbCommand command, string name, string value)
    {
        DbParameter parameter = command.CreateParameter();
        parameter.ParameterName = name;
        parameter.Value = value;
        command.Parameters.Add(parameter);
    }
}
