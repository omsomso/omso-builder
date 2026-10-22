import SwiftUI
import AppKit

struct PullRequest: Decodable, Equatable {
    let number: Int
    let state: String
    let isDraft: Bool
    let url: String
    let headRefName: String
    let updatedAt: String
}

enum BranchStatus: Equatable {
    case main, open(Int), draft(Int), merged(Int), closed(Int), noPullRequest

    var label: String {
        switch self {
        case .main: return "main"
        case .open(let number): return "Open #\(number)"
        case .draft(let number): return "Draft #\(number)"
        case .merged(let number): return "Merged #\(number)"
        case .closed(let number): return "Closed #\(number)"
        case .noPullRequest: return "No PR"
        }
    }

    var color: Color {
        switch self {
        case .main: return .blue
        case .open: return .green
        case .draft: return .gray
        case .merged: return .purple
        case .closed: return .red
        case .noPullRequest: return .secondary
        }
    }
}

enum SourceOrdering {
    /// Newest checked-out commit first; branch name breaks ties so the order is stable.
    static func sorted(_ sources: [BuildSource]) -> [BuildSource] {
        sources.sorted { ($0.updated ?? 0, $1.branch) > ($1.updated ?? 0, $0.branch) }
    }

    /// Every space-separated word must appear in the branch name or folder path.
    static func filter(_ sources: [BuildSource], query: String) -> [BuildSource] {
        let words = query.lowercased().split(whereSeparator: \.isWhitespace)
        guard !words.isEmpty else { return sources }
        return sources.filter { source in
            let text = (source.branch + " " + source.path).lowercased()
            return words.allSatisfy { text.contains($0) }
        }
    }

    /// Prefers an open pull request, otherwise the most recently updated one for the branch.
    static func pullRequest(branch: String, pulls: [PullRequest]) -> PullRequest? {
        let matches = pulls.filter { $0.headRefName == branch }
        return matches.first { $0.state == "OPEN" } ?? matches.max { $0.updatedAt < $1.updatedAt }
    }

    static func status(branch: String, pulls: [PullRequest]) -> BranchStatus {
        guard let pull = pullRequest(branch: branch, pulls: pulls) else {
            return branch == "main" ? .main : .noPullRequest
        }
        switch pull.state {
        case "MERGED": return .merged(pull.number)
        case "CLOSED": return .closed(pull.number)
        default: return pull.isDraft ? .draft(pull.number) : .open(pull.number)
        }
    }
}

/// Pull request states come from the GitHub CLI, for the repository shown. Building never
/// depends on them: without gh, network or sign-in the picker simply shows no badges.
final class PullRequestStore: ObservableObject {
    @Published private(set) var pulls: [PullRequest]?
    @Published private(set) var problem: String?
    private var root: URL?
    private var loading: URL?

    func refresh(root: URL) {
        if root != self.root { pulls = nil; problem = nil; self.root = root }
        guard loading != root else { return }
        loading = root
        DispatchQueue.global(qos: .utility).async {
            let result = Result { try Self.fetch(root: root) }
            DispatchQueue.main.async {
                if self.loading == root { self.loading = nil }
                guard root == self.root else { return }
                switch result {
                case .success(let pulls): self.pulls = pulls; self.problem = nil
                case .failure(let error): self.problem = error.localizedDescription
                }
            }
        }
    }

    func status(for source: BuildSource) -> BranchStatus? {
        pulls.map { SourceOrdering.status(branch: source.branch, pulls: $0) }
    }

    func pullRequest(for source: BuildSource) -> PullRequest? {
        pulls.flatMap { SourceOrdering.pullRequest(branch: source.branch, pulls: $0) }
    }

    private static func fetch(root: URL) throws -> [PullRequest] {
        guard let gh = ["/opt/homebrew/bin/gh", "/usr/local/bin/gh"].first(where: FileManager.default.isExecutableFile) else {
            throw Builder.error("PR status needs the GitHub CLI (gh).")
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: gh)
        process.arguments = ["pr", "list", "--state", "all", "--limit", "300",
                             "--json", "number,state,isDraft,url,headRefName,updatedAt"]
        process.currentDirectoryURL = root
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        environment["GH_PROMPT_DISABLED"] = "1"
        process.environment = environment
        let output = Pipe()
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        try process.run()
        DispatchQueue.global().asyncAfter(deadline: .now() + 20) { if process.isRunning { process.terminate() } }
        let data = output.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw Builder.error("PR status unavailable. Check gh sign-in and network.")
        }
        return try JSONDecoder().decode([PullRequest].self, from: data)
    }
}

struct StatusBadge: View {
    let status: BranchStatus

    var body: some View {
        HStack(spacing: 4) {
            if status == .noPullRequest {
                Circle().strokeBorder(status.color, lineWidth: 1).frame(width: 7, height: 7)
            } else {
                Circle().fill(status.color).frame(width: 7, height: 7)
            }
            Text(status.label).font(.caption2.weight(.medium))
        }
        .padding(.horizontal, 6).padding(.vertical, 2)
        .background(status.color.opacity(0.14), in: Capsule())
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Pull request status: \(status.label)")
    }
}

struct SourcePicker: View {
    let sources: [BuildSource]
    @Binding var selection: String
    @ObservedObject var pulls: PullRequestStore
    @State private var showing = false
    @State private var query = ""
    @FocusState private var searchFocused: Bool

    private static let dates: RelativeDateTimeFormatter = {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        return formatter
    }()

    private var current: BuildSource? { sources.first { $0.path == selection } }
    private var matches: [BuildSource] { SourceOrdering.filter(SourceOrdering.sorted(sources), query: query) }

    static func relative(_ time: TimeInterval) -> String {
        dates.localizedString(for: Date(timeIntervalSince1970: time), relativeTo: Date())
    }

    static func age(_ source: BuildSource) -> String? { source.updated.map(relative) }

    var body: some View {
        HStack {
            Text("Build from")
            Button { query = ""; showing = true } label: {
                HStack(spacing: 8) {
                    if let current {
                        Text(current.title).lineLimit(1).truncationMode(.middle)
                        if let status = pulls.status(for: current) { StatusBadge(status: status) }
                    } else {
                        Text("Source unavailable — choose another").foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 4)
                    Image(systemName: "chevron.up.chevron.down").font(.caption).foregroundStyle(.secondary)
                }.frame(maxWidth: .infinity).contentShape(Rectangle())
            }
            .help("Search checked-out branches, newest first")
            .popover(isPresented: $showing, arrowEdge: .bottom) { list }
        }
    }

    var list: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("Search branches", text: $query)
                .textFieldStyle(.roundedBorder).focused($searchFocused)
                .onSubmit { if let first = matches.first { choose(first) } }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 2) {
                    ForEach(matches) { source in row(source) }
                    if matches.isEmpty {
                        Text("No checked-out branch matches “\(query)”.").foregroundStyle(.secondary).padding(8)
                    }
                }
            }.frame(height: min(360, CGFloat(max(matches.count, 1)) * 44 + 8))
            Divider()
            HStack {
                Text("\(matches.count) of \(sources.count) branches · newest first")
                Spacer()
                if pulls.pulls == nil && pulls.problem == nil { Text("Loading PR status…") }
            }.font(.caption).foregroundStyle(.secondary)
            if let problem = pulls.problem {
                Text(problem).font(.caption).foregroundStyle(.secondary)
            }
            Text("Only branches checked out in a folder are listed. Return picks the top match.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(12).frame(width: 440)
        .onAppear { searchFocused = true }
    }

    private func row(_ source: BuildSource) -> some View {
        let chosen = source.path == selection
        return Button { choose(source) } label: {
            HStack(spacing: 8) {
                Image(systemName: "checkmark").font(.caption.bold()).opacity(chosen ? 1 : 0)
                VStack(alignment: .leading, spacing: 2) {
                    Text(source.branch).lineLimit(1).truncationMode(.middle)
                    Text(([Self.age(source), source.primary ? "main folder" : nil, source.dirty ? "local edits" : nil]
                        .compactMap { $0 }).joined(separator: " · "))
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 6)
                if let status = pulls.status(for: source) { StatusBadge(status: status) }
            }
            .padding(.horizontal, 8).padding(.vertical, 5).frame(maxWidth: .infinity, alignment: .leading)
            .background(chosen ? Color.accentColor.opacity(0.15) : Color.clear, in: RoundedRectangle(cornerRadius: 6))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain).help(source.path)
        .contextMenu {
            if let pull = pulls.pullRequest(for: source), let url = URL(string: pull.url) {
                Button("Open pull request #\(pull.number)") { NSWorkspace.shared.open(url) }
            }
            Button("Reveal folder in Finder") { NSWorkspace.shared.activateFileViewerSelecting([source.root]) }
        }
    }

    private func choose(_ source: BuildSource) {
        selection = source.path
        showing = false
    }
}
