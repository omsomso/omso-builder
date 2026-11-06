import SwiftUI
import AppKit

struct BuildOption: Decodable, Identifiable, Equatable {
    let id: String
    let label: String
    let help: String
    let `default`: Bool
    let products: [String]
}

struct ManifestUse: Decodable, Equatable {
    let path: String
    let kind: String
}

struct BuildSource: Decodable, Identifiable, Equatable {
    let path: String
    let branch: String
    let head: String
    let dirty: Bool
    let primary: Bool
    let products: [String]
    var options: [BuildOption] = []
    var notes: [String] = []
    var manifest: ManifestUse? = nil
    var problem: String? = nil
    var updated: TimeInterval? = nil
    var id: String { path }
    var root: URL { URL(fileURLWithPath: path, isDirectory: true) }
    var title: String { branch + (primary ? " — main folder" : "") }

    enum CodingKeys: String, CodingKey { case path, branch, head, dirty, primary, products, options, notes, manifest, problem, updated }

    init(path: String, branch: String, head: String, dirty: Bool, primary: Bool, products: [String],
         options: [BuildOption] = [], notes: [String] = [], manifest: ManifestUse? = nil, updated: TimeInterval? = nil) {
        self.path = path; self.branch = branch; self.head = head; self.dirty = dirty; self.primary = primary
        self.products = products; self.options = options; self.notes = notes; self.manifest = manifest; self.updated = updated
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        path = try values.decode(String.self, forKey: .path)
        branch = try values.decode(String.self, forKey: .branch)
        head = try values.decode(String.self, forKey: .head)
        dirty = try values.decode(Bool.self, forKey: .dirty)
        primary = try values.decode(Bool.self, forKey: .primary)
        products = try values.decodeIfPresent([String].self, forKey: .products) ?? []
        options = try values.decodeIfPresent([BuildOption].self, forKey: .options) ?? []
        notes = try values.decodeIfPresent([String].self, forKey: .notes) ?? []
        manifest = try values.decodeIfPresent(ManifestUse.self, forKey: .manifest)
        problem = try values.decodeIfPresent(String.self, forKey: .problem)
        updated = try values.decodeIfPresent(TimeInterval.self, forKey: .updated)
    }
}

struct Repository: Decodable, Identifiable, Equatable {
    let path: String
    let name: String
    let problem: String?
    let sources: [BuildSource]
    var id: String { path }
    var root: URL { URL(fileURLWithPath: path, isDirectory: true) }
}

/// The builder core in this project: every request goes through scripts/build-api.py.
struct Builder {
    static let python = "/usr/bin/python3"
    static let environment: [String: String] = {
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        environment["PYTHONUNBUFFERED"] = "1"
        return environment
    }()
    let root: URL
    var api: URL { root.appendingPathComponent("scripts/build-api.py") }

    init(bundle: Bundle = .main) throws {
        guard let resource = bundle.url(forResource: "project-path", withExtension: "txt") else {
            throw Self.error("The app is missing its project folder. Rebuild it with scripts/build-app.sh.")
        }
        root = URL(fileURLWithPath: try String(contentsOf: resource, encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines), isDirectory: true)
        guard FileManager.default.fileExists(atPath: api.path) else {
            throw Self.error("The omso-builder folder has moved. Rebuild the app from its new location.")
        }
    }

    static func error(_ message: String) -> NSError {
        NSError(domain: "OmsoBuilder", code: 1, userInfo: [NSLocalizedDescriptionKey: message])
    }

    static func quote(_ value: String) -> String { "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'" }

    /// One JSON request to the builder API; failures still return its JSON error body.
    func call(_ request: [String: Any]) throws -> Data {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: Self.python)
        process.arguments = [api.path]
        process.environment = Self.environment
        let input = Pipe(), output = Pipe()
        process.standardInput = input
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        try process.run()
        try input.fileHandleForWriting.write(contentsOf: try JSONSerialization.data(withJSONObject: request))
        try input.fileHandleForWriting.close()
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        return data
    }

    private struct SourcesResponse: Decodable {
        struct Failure: Decodable { let message: String }
        let ok: Bool
        let repositories: [Repository]?
        let error: Failure?
    }

    func repositories() throws -> [Repository] {
        let response = try JSONDecoder().decode(SourcesResponse.self, from: call(["api_version": 2, "operation": "sources"]))
        guard response.ok, let repositories = response.repositories else {
            throw Self.error("Could not list repositories. " + (response.error?.message ?? ""))
        }
        return repositories
    }

    func add(repository url: URL) throws {
        struct Added: Decodable { struct Failure: Decodable { let message: String }; let ok: Bool; let error: Failure? }
        let response = try JSONDecoder().decode(Added.self, from: call(["api_version": 2, "operation": "add_repository", "path": url.path]))
        guard response.ok else { throw Self.error(response.error?.message ?? "Could not add that folder.") }
    }

    /// Log line when the folder moved to another commit or branch since it was last listed.
    static func sourceChange(from old: BuildSource?, to new: BuildSource) -> String? {
        guard let old, old.path == new.path, old.head != new.head || old.branch != new.branch else { return nil }
        return "Source updated since the last refresh: \(old.branch) @ \(old.head.prefix(8)) → \(new.branch) @ \(new.head.prefix(8))."
    }

    /// Options that apply to the selection (an option naming products applies only when one is selected).
    static func applicable(_ option: BuildOption, selected: Set<String>) -> Bool {
        option.products.isEmpty || !selected.isDisjoint(with: option.products)
    }

    func request(source: BuildSource, selected: Set<String>, options: [String: Bool], preview: Bool) throws -> Data {
        guard !selected.isEmpty, selected.isSubset(of: Set(source.products)) else {
            throw Self.error("Select products available in this source version.")
        }
        let known = Dictionary(uniqueKeysWithValues: source.options.map { ($0.id, options[$0.id] ?? $0.default) })
        return try JSONSerialization.data(withJSONObject: [
            "api_version": 2, "operation": "build", "source_dir": source.path,
            "products": source.products.filter(selected.contains), "options": known,
            "expected_head": source.head, "expected_branch": source.branch,
            "dry_run": preview, "install_mode": "deferred"
        ] as [String: Any])
    }
}

struct BuilderView: View {
    let builder: Builder
    @State private var repositories: [Repository]
    @State private var repositoryPath: String
    @State private var sourcePath: String
    @State private var selected: Set<String>
    @State private var options: [String: Bool]
    @StateObject private var runner = BuildRunner()
    @StateObject private var pulls = PullRequestStore()
    @StateObject private var ready = ReadyBuildStore()
    @State private var checking = false
    @State private var loadGeneration = 0

    private var repository: Repository? { repositories.first { $0.path == repositoryPath } }
    private var sources: [BuildSource] { repository?.sources ?? [] }
    private var source: BuildSource? { sources.first { $0.path == sourcePath } }
    private var products: [String] { source?.products ?? [] }
    private var buildable: Bool { source != nil && source?.problem == nil && !selected.isEmpty && !runner.running && !checking }

    init(builder: Builder, repositories: [Repository]) {
        self.builder = builder
        let defaults = UserDefaults.standard
        let repository = repositories.first { $0.path == defaults.string(forKey: "selectedRepository") } ?? repositories.first
        _repositories = State(initialValue: repositories)
        _repositoryPath = State(initialValue: repository?.path ?? "")
        let source = repository.flatMap { Self.initialSource($0) }
        _sourcePath = State(initialValue: source?.path ?? "")
        _selected = State(initialValue: Self.initialSelection(repository: repository, source: source))
        _options = State(initialValue: repository.map { Self.savedOptions($0.path) } ?? [:])
    }

    private static func initialSource(_ repository: Repository) -> BuildSource? {
        let saved = UserDefaults.standard.string(forKey: "selectedSource." + repository.path)
        return repository.sources.first { $0.path == saved } ?? repository.sources.first { $0.primary } ?? repository.sources.first
    }

    private static func initialSelection(repository: Repository?, source: BuildSource?) -> Set<String> {
        guard let repository, let source else { return [] }
        let saved = UserDefaults.standard.stringArray(forKey: "selectedProducts." + repository.path)
        return Set(saved ?? source.products).intersection(source.products)
    }

    private static func savedOptions(_ repository: String) -> [String: Bool] {
        (UserDefaults.standard.dictionary(forKey: "options." + repository) as? [String: Bool]) ?? [:]
    }

    var body: some View {
        HStack(alignment: .top, spacing: 22) {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 12) {
                    Image(systemName: "hammer.circle.fill").font(.system(size: 38)).foregroundStyle(.tint)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("omso Builder").font(.title2.bold())
                        Text("Build, test and install from any checkout").foregroundStyle(.secondary)
                    }
                    Spacer()
                }
                HStack {
                    if repositories.isEmpty {
                        Text("No repositories yet.").foregroundStyle(.secondary)
                    } else {
                        // A menu scales to any number of registered repositories; a segmented control does not.
                        Text("Repository")
                        Picker("Repository", selection: $repositoryPath) {
                            ForEach(repositories) { Text($0.name).tag($0.path) }
                        }.pickerStyle(.menu).labelsHidden().frame(maxWidth: .infinity, alignment: .leading)
                    }
                    Button("Add…") { addRepository() }.disabled(runner.running || checking)
                        .help("Register another Git repository that has an omso-build.json")
                }
                if let problem = repository?.problem {
                    Text(problem).font(.caption).foregroundStyle(.red)
                }
                sourceSection
                productSection
                Divider()
                HStack {
                    Button("Open /Applications/omso") { NSWorkspace.shared.open(URL(fileURLWithPath: "/Applications/omso")) }
                        .disabled(!FileManager.default.fileExists(atPath: "/Applications/omso"))
                    Spacer()
                    Button("Preview") { start(preview: true) }.disabled(!buildable)
                    Button("Build & Install") { start(preview: false) }.buttonStyle(.borderedProminent).disabled(!buildable)
                }
                Text(runner.message).font(.caption).foregroundStyle(runner.failure ? Color.red : Color.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }.frame(width: 480)
            Divider()
            VStack(alignment: .leading, spacing: 12) {
                ReadyBuildsView(store: ready, busy: runner.running, refresh: { ready.refresh(builder: builder) },
                                install: { runner.installReady(builder: builder, build: $0) })
                Divider()
                HStack {
                    Text("Build log").font(.headline)
                    if runner.running { ProgressView().controlSize(.small) }
                    Spacer()
                    Button("Show log") { if let url = runner.logURL { NSWorkspace.shared.open(url) } }
                        .disabled(runner.logURL == nil)
                }
                ScrollViewReader { reader in
                    ScrollView {
                        Text(runner.log).font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading).padding(12)
                        Color.clear.frame(height: 1).id("end")
                    }.background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
                    .onChange(of: runner.log) { _ in reader.scrollTo("end", anchor: .bottom) }
                }
                if runner.canInstall {
                    Button("Retry installation") { runner.install() }.disabled(runner.running)
                }
                Text("Installation copies an already tested build and is usually quick. A full build also compiles and tests.")
                    .font(.caption).foregroundStyle(.secondary)
            }.frame(minWidth: 380)
        }.padding(24).frame(minWidth: 980, idealWidth: 1060, minHeight: 820)
            .onChange(of: repositoryPath) { path in switchRepository(path) }
            .onChange(of: sourcePath) { _ in selected.formIntersection(Set(products)) }
            .onAppear { if let repository { pulls.refresh(root: repository.root) }; ready.refresh(builder: builder) }
            .onChange(of: runner.running) { running in if !running { ready.refresh(builder: builder) } }
            // Commits, merges and branch switches happen while the app stays open; keep the display current.
            .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
                guard !runner.running && !checking else { return }
                loadSources { _ in }
                ready.refresh(builder: builder)
            }
    }

    @ViewBuilder private var sourceSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                SourcePicker(sources: sources, selection: $sourcePath, pulls: pulls)
                Button("Refresh") { refresh() }.disabled(runner.running || checking)
            }
            if let source {
                Text(source.path).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    .truncationMode(.middle).help(source.path)
                HStack(spacing: 4) {
                    Text("Version \(source.head.prefix(8))" + (SourcePicker.age(source).map { " (\($0))" } ?? "")
                         + (source.dirty ? " · Includes local edits" : " · No local edits"))
                    if let pull = pulls.pullRequest(for: source), let url = URL(string: pull.url) {
                        Text("·")
                        Link("Pull request #\(pull.number)", destination: url)
                    }
                }.font(.caption).foregroundStyle(.secondary)
                if let problem = source.problem {
                    Text(problem).font(.caption).foregroundStyle(.red)
                } else if let manifest = source.manifest, manifest.kind == "tooling" {
                    Text("No omso-build.json here; built with the one in \(manifest.path)")
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                        .help(manifest.path)
                }
            }
        }
    }

    @ViewBuilder private var productSection: some View {
        HStack {
            Text("\(selected.count) of \(products.count) selected").font(.subheadline)
            Spacer()
            Button("All") { selected = Set(products) }
            Button("None") { selected = [] }
        }
        ScrollView {
            LazyVGrid(columns: [GridItem(.flexible(), alignment: .leading), GridItem(.flexible(), alignment: .leading)],
                      alignment: .leading, spacing: 14) {
                ForEach(products, id: \.self) { product in
                    Toggle(product, isOn: Binding(get: { selected.contains(product) }, set: { on in
                        if on { selected.insert(product) } else { selected.remove(product) }
                    })).toggleStyle(.checkbox)
                }
            }.padding(14)
        }.frame(minHeight: 200).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
        ForEach(source?.options ?? []) { option in
            VStack(alignment: .leading, spacing: 5) {
                Toggle(option.label, isOn: Binding(get: { options[option.id] ?? option.default }, set: { value in
                    options[option.id] = value
                    UserDefaults.standard.set(options, forKey: "options." + repositoryPath)
                })).toggleStyle(.checkbox).disabled(!Builder.applicable(option, selected: selected))
                if !option.help.isEmpty {
                    Text(option.help).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        Text("Builds and tests here, then installs with a macOS approval dialog when needed.")
            .font(.callout).foregroundStyle(.secondary)
        ForEach(source?.notes ?? [], id: \.self) { note in
            Text(note).font(.caption).foregroundStyle(.secondary)
        }
    }

    private func switchRepository(_ path: String) {
        guard let repository else { return }
        UserDefaults.standard.set(path, forKey: "selectedRepository")
        let source = Self.initialSource(repository)
        sourcePath = source?.path ?? ""
        selected = Self.initialSelection(repository: repository, source: source)
        options = Self.savedOptions(path)
        pulls.refresh(root: repository.root)
    }

    /// Lists sources off the main thread; only the newest request updates the window.
    private func loadSources(_ done: @escaping (Result<Void, Error>) -> Void) {
        loadGeneration += 1
        let generation = loadGeneration, builder = builder
        DispatchQueue.global(qos: .userInitiated).async {
            let result = Result { try builder.repositories() }
            DispatchQueue.main.async {
                guard generation == loadGeneration else { return }
                switch result {
                case .success(let fresh):
                    let hadRepository = repository != nil
                    repositories = fresh
                    if !hadRepository || repository == nil, let first = fresh.first {
                        repositoryPath = first.path
                        switchRepository(first.path)
                    }
                    selected.formIntersection(Set(products))
                    done(.success(()))
                case .failure(let error): done(.failure(error))
                }
            }
        }
    }

    private func refresh() {
        if let repository { pulls.refresh(root: repository.root) }
        ready.refresh(builder: builder)
        loadSources { result in
            switch result {
            case .success:
                runner.failure = source == nil
                runner.message = source == nil ? "That source folder is no longer available. Choose another version."
                    : "Source versions refreshed. Local edits are included when you build."
            case .failure(let error):
                runner.failure = true
                runner.message = error.localizedDescription
            }
        }
    }

    private func addRepository() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
        panel.message = "Choose a checkout of a Git repository that has an omso-build.json."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            try builder.add(repository: url)
            loadSources { _ in
                if let added = repositories.first(where: { url.path.hasPrefix($0.path) }) { repositoryPath = added.path }
            }
        } catch {
            runner.failure = true; runner.message = error.localizedDescription
        }
    }

    /// Always builds the selected folder as it is now: sources are reloaded first,
    /// so new commits or a branch switch since the last refresh never block a build.
    private func start(preview: Bool) {
        guard !checking, !runner.running else { return }
        let before = source
        checking = true
        runner.failure = false
        runner.message = "Checking the selected source…"
        loadSources { result in
            checking = false
            if case .failure(let error) = result {
                runner.failure = true; runner.message = error.localizedDescription; return
            }
            guard let source, source.problem == nil else {
                runner.failure = true
                runner.message = source?.problem ?? "That source folder is no longer available. Choose another version."
                return
            }
            guard !selected.isEmpty else {
                runner.failure = true
                runner.message = "The selected products are not in this source version. Choose products and build again."
                return
            }
            runner.start(builder: builder, source: source, selected: selected, options: options, preview: preview)
            if runner.running, let change = Builder.sourceChange(from: before, to: source) {
                runner.note(change + "\n")
            }
            UserDefaults.standard.set(products.filter(selected.contains), forKey: "selectedProducts." + repositoryPath)
            UserDefaults.standard.set(source.path, forKey: "selectedSource." + repositoryPath)
        }
    }
}

final class BuilderAppDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard BuildRunner.isBusy else { return .terminateNow }
        let alert = NSAlert()
        alert.messageText = "A build or installation is still running."
        alert.informativeText = "Wait for it to finish before quitting the builder."
        alert.runModal()
        return .terminateCancel
    }
}

enum SelfTest {
    static func run(builder: Builder) throws {
        try BuildRunner.selfTest()
        struct Listed: Decodable { let ok: Bool; let repositories: [[String: String]]? }
        let listed = try JSONDecoder().decode(Listed.self, from: builder.call(["api_version": 2, "operation": "repositories"]))
        precondition(listed.ok, "The builder API did not answer")
        precondition(Builder.quote("a'b") == "'a'\\''b'")
        let listedSource = BuildSource(path: "/tmp/main", branch: "main", head: "aaaaaaaa1", dirty: false, primary: true, products: [])
        let moved = BuildSource(path: "/tmp/main", branch: "main", head: "bbbbbbbb2", dirty: true, primary: true, products: [])
        precondition(Builder.sourceChange(from: listedSource, to: listedSource) == nil)
        precondition(Builder.sourceChange(from: nil, to: moved) == nil)
        precondition(Builder.sourceChange(from: listedSource, to: moved) == "Source updated since the last refresh: main @ aaaaaaaa → main @ bbbbbbbb.")
        let auv3 = BuildOption(id: "auv3", label: "AUv3", help: "", default: true, products: ["DrumLab"])
        let source = BuildSource(path: "/tmp/Bands user's branch", branch: "codex/bands", head: "1234", dirty: true,
                                 primary: true, products: ["Utility", "Bands", "Clipper", "DrumLab"], options: [auv3])
        let data = try builder.request(source: source, selected: ["Clipper", "Bands"], options: ["auv3": false], preview: true)
        let request = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        precondition(request["source_dir"] as? String == source.path)
        precondition(request["products"] as? [String] == ["Bands", "Clipper"], "Products keep the checkout's order")
        precondition(request["install_mode"] as? String == "deferred")
        precondition(request["dry_run"] as? Bool == true)
        precondition(request["expected_head"] as? String == source.head)
        precondition((request["options"] as? [String: Bool]) == ["auv3": false])
        let defaults = try JSONSerialization.jsonObject(with: try builder.request(source: source, selected: ["DrumLab"], options: [:], preview: false)) as! [String: Any]
        precondition((defaults["options"] as? [String: Bool]) == ["auv3": true], "Unset options use the manifest default")
        precondition(!Builder.applicable(auv3, selected: ["Bands"]) && Builder.applicable(auv3, selected: ["DrumLab"]))
        do { _ = try builder.request(source: source, selected: ["Missing"], options: [:], preview: true); preconditionFailure("Unknown product accepted") } catch {}
        func fixture(_ branch: String, _ updated: TimeInterval?) -> BuildSource {
            BuildSource(path: "/tmp/VSTS " + branch, branch: branch, head: "1", dirty: false, primary: false, products: [], updated: updated)
        }
        let ordered = SourceOrdering.sorted([fixture("old", 10), fixture("unknown", nil), fixture("b-new", 30), fixture("a-new", 30)])
        precondition(ordered.map(\.branch) == ["a-new", "b-new", "old", "unknown"])
        precondition(SourceOrdering.filter(ordered, query: " NEW  a ").map(\.branch) == ["a-new"])
        precondition(SourceOrdering.filter(ordered, query: "").count == 4)
        precondition(SourceOrdering.filter(ordered, query: "missing").isEmpty)
        func pull(_ number: Int, _ state: String, draft: Bool = false, branch: String = "codex/x", at: String) -> PullRequest {
            PullRequest(number: number, state: state, isDraft: draft, url: "https://example.invalid/\(number)",
                        headRefName: branch, updatedAt: at)
        }
        let pullList = [pull(1, "CLOSED", at: "2026-01-01T00:00:00Z"), pull(2, "MERGED", at: "2026-02-01T00:00:00Z"),
                        pull(3, "OPEN", draft: true, branch: "codex/y", at: "2026-01-01T00:00:00Z"),
                        pull(4, "CLOSED", branch: "codex/z", at: "2026-03-01T00:00:00Z"),
                        pull(5, "OPEN", branch: "codex/z", at: "2026-01-01T00:00:00Z")]
        precondition(SourceOrdering.status(branch: "codex/x", pulls: pullList) == .merged(2))
        precondition(SourceOrdering.status(branch: "codex/y", pulls: pullList) == .draft(3))
        precondition(SourceOrdering.status(branch: "codex/z", pulls: pullList) == .open(5))
        precondition(SourceOrdering.status(branch: "codex/none", pulls: pullList) == .noPullRequest)
        precondition(SourceOrdering.status(branch: "main", pulls: pullList) == .main)
        let sourcesJSON = """
            {"ok": true, "repositories": [{"path": "/r/VSTS", "name": "omso audio plugins", "tooling": "/r/VSTS", "problem": null,
              "sources": [{"path": "/r/VSTS", "branch": "main", "head": "h", "dirty": false, "primary": true, "updated": 5,
                           "products": ["Bands"], "options": [{"id": "auv3", "label": "AUv3", "help": "", "default": true, "products": []}],
                           "notes": ["n"], "manifest": {"path": "/r/VSTS", "kind": "own"}, "problem": null},
                          {"path": "/r/old", "branch": "old", "head": "h", "dirty": false, "primary": false, "updated": null,
                           "products": [], "options": [], "notes": [], "manifest": null, "problem": "No omso-build.json"}]}]}
            """
        struct SourcesFixture: Decodable { let repositories: [Repository] }
        let repositories = try JSONDecoder().decode(SourcesFixture.self, from: Data(sourcesJSON.utf8)).repositories
        precondition(repositories[0].sources[0].options[0].id == "auv3" && repositories[0].sources[1].problem != nil)
        let readyJSON = """
            {"ok": true, "builds": [
             {"repository": {"path": "/r/VSTS", "name": "omso audio plugins"},
              "source": {"path": "/tmp/a", "branch": "a", "head": "new", "dirty": false, "primary": false, "products": ["Bands"]},
              "id": "dev", "configuration": "Debug", "build_id": "1", "version": "0.1.1", "revision": "old",
              "dirty": false, "created": 100, "products": ["Bands"], "installed": [], "installer_running": true},
             {"repository": {"path": "/r/VSTS", "name": "omso audio plugins"},
              "source": {"path": "/tmp/b", "branch": "b", "head": "rev", "dirty": false, "primary": false, "products": ["Bands"]},
              "id": "release", "configuration": "Release", "build_id": "2", "version": "0.1.1", "revision": "rev",
              "dirty": false, "created": 300, "products": ["Bands"], "installed": ["Bands"], "installer_running": false},
             {"repository": {"path": "/r/omso-daw", "name": "omso DAW"},
              "source": {"path": "/r/omso-daw", "branch": "Detached HEAD", "head": "rev", "dirty": false, "primary": true, "products": ["omso DAW"]},
              "id": "omso DAW", "configuration": "Release", "build_id": "3", "version": "", "revision": "",
              "dirty": false, "created": 200, "products": ["omso DAW"], "installed": [], "installer_running": false}]}
            """
        struct ReadyFixture: Decodable { let builds: [ReadyBuild] }
        let readyBuilds = ReadyBuild.ordered(try JSONDecoder().decode(ReadyFixture.self, from: Data(readyJSON.utf8)).builds)
        // Waiting builds first, newest first; installed builds after them.
        precondition(readyBuilds.map(\.build_id) == ["3", "1", "2"])
        precondition(!readyBuilds[1].isCurrent && readyBuilds[0].isCurrent && readyBuilds[2].isInstalled && !readyBuilds[0].isInstalled)
        precondition(readyBuilds[0].name == "r (detached)" && readyBuilds[0].key == "omso DAW")
    }
}

@main
struct OmsoBuilderApp: App {
    @NSApplicationDelegateAdaptor(BuilderAppDelegate.self) private var delegate
    private let builder: Builder?
    private let repositories: [Repository]
    private let error: String?

    init() {
        let builder: Builder?
        var repositories: [Repository] = []
        var problem: String?
        do { builder = try Builder() } catch { builder = nil; problem = error.localizedDescription }
        if CommandLine.arguments.contains("--self-test") {
            guard let builder else { fatalError(problem ?? "The builder folder is unavailable.") }
            do { try SelfTest.run(builder: builder) } catch { fatalError(error.localizedDescription) }
            print("omso Builder checks passed.")
            exit(0)
        }
        if let builder, problem == nil {
            do { repositories = try builder.repositories() } catch { problem = error.localizedDescription }
        }
        self.builder = builder
        self.repositories = repositories
        error = problem
    }

    var body: some Scene {
        Window("omso Builder", id: "builder") {
            if let builder, error == nil { BuilderView(builder: builder, repositories: repositories) }
            else { Text(error ?? "Builder folder unavailable.").padding(30).frame(width: 480) }
        }.defaultSize(width: 1060, height: 900)
    }
}
