using ApiTestTool.MockBank;

// Standalone MockBank. The same app can also run inside the CLI: `aqt mock` or `aqt run --bank MOCK --with-mock`.
MockBankHost.Create(args).Run();
