import SwiftUI
import AppKit

/// A finished, tested build in some checkout of a registered repository,
/// for example one left by a session whose own installation never completed.
struct ReadyBuild: Decodable, Identifiable, Equatable {
    struct RepositoryName: Decodable, Equatable { let path: String; let name: String }
    let repository: RepositoryName
    let source: BuildSource
    let key: String
    let build_id: String
    let products: [String]
    let configuration: String
    let version: String
    let revision: String
    let dirty: Bool
    let created: TimeInterval
    let installed: [String]
    let installer_running: Bool

    enum CodingKeys: String, CodingKey {
        case repository, source, key = "id", build_id, products, configuration, version, revision, dirty, created,
             installed, installer_running
    }

    var id: String { source.path + "\n" + key }
    var isInstalled: Bool { Set(installed) == Set(products) }
    var isCurrent: Bool { revision.isEmpty || revision == source.head }
    /// Detached checkouts are named by their folder; worktree folders are often all named after the repository.
    var name: String {
        guard source.branch == "Detached HEAD" else { return source.branch }
        let repositoryFolder = URL(fileURLWithPath: repository.path).lastPathComponent
        let folder = source.root.lastPathComponent == repositoryFolder ? source.root.deletingLastPathComponent() : source.root
        return folder.lastPathComponent + " (detached)"
    }

    static func ordered(_ builds: [ReadyBuild]) -> [ReadyBuild] {
        // Builds still waiting for installation first, then newest first.
        builds.sorted { ($0.isInstalled ? 0 : 1, $0.created, $1.id) > ($1.isInstalled ? 0 : 1, $1.created, $0.id) }
    }
}

private struct ReadyResponse: Decodable {
    struct Failure: Decodable { let message: String }
    let ok: Bool
    let builds: [ReadyBuild]?
    let problems: [String]?
    let error: Failure?
}

final class ReadyBuildStore: ObservableObject {
    @Published private(set) var builds: [ReadyBuild] = []
    @Published private(set) var problem: String?
    @Published private(set) var loading = false

    func refresh(builder: Builder) {
        guard !loading else { return }
        loading = true
        DispatchQueue.global(qos: .utility).async {
            let result = Result { () throws -> ([ReadyBuild], [String]) in
                let response = try JSONDecoder().decode(ReadyResponse.self,
                    from: try builder.call(["api_version": 2, "operation": "ready"]))
                guard response.ok, let builds = response.builds else {
                    throw Builder.error(response.error?.message ?? "Could not list ready builds.")
                }
                return (ReadyBuild.ordered(builds), response.problems ?? [])
            }
            DispatchQueue.main.async {
                self.loading = false
                switch result {
                case .success(let (builds, problems)):
                    self.builds = builds; self.problem = problems.isEmpty ? nil : problems.joined(separator: "\n")
                case .failure(let error): self.problem = error.localizedDescription
                }
            }
        }
    }
}

struct ReadyBuildsView: View {
    @ObservedObject var store: ReadyBuildStore
    let busy: Bool
    let refresh: () -> Void
    let install: (ReadyBuild) -> Void

    private var waiting: Int { store.builds.filter { !$0.isInstalled }.count }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Ready to install").font(.headline)
                if waiting > 0 {
                    Text("\(waiting)").font(.caption.bold()).padding(.horizontal, 6).padding(.vertical, 1)
                        .background(Color.accentColor.opacity(0.2), in: Capsule())
                }
                if store.loading { ProgressView().controlSize(.small) }
                Spacer()
                Button("Refresh", action: refresh).disabled(store.loading)
            }
            if store.builds.isEmpty {
                Text(store.problem ?? (store.loading ? "Looking for finished builds…"
                     : "No finished builds found. Builds appear here once they pass their tests."))
                    .font(.caption).foregroundStyle(.secondary).frame(maxWidth: .infinity, alignment: .leading).padding(10)
                    .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 2) {
                        ForEach(store.builds) { build in row(build) }
                    }.padding(4)
                }
                .frame(height: min(230, CGFloat(store.builds.count) * 50 + 8))
                .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
                if let problem = store.problem {
                    Text(problem).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                }
            }
        }
    }

    private func row(_ build: ReadyBuild) -> some View {
        let notes = [build.repository.name, "built " + SourcePicker.relative(build.created),
                     build.configuration == "Debug" ? "Debug build" : nil,
                     build.isCurrent ? nil : "older commit than checkout",
                     build.dirty ? "had local edits" : nil,
                     build.installer_running && !build.isInstalled ? "terminal install pending" : nil]
            .compactMap { $0 }.joined(separator: " · ")
        return HStack(spacing: 8) {
            Image(systemName: build.isInstalled ? "checkmark.circle.fill" : "shippingbox.fill")
                .foregroundStyle(build.isInstalled ? Color.secondary : Color.accentColor)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 4) {
                    Text(build.name).lineLimit(1).truncationMode(.middle)
                    Text("· " + build.products.joined(separator: ", ")).foregroundStyle(.secondary)
                        .lineLimit(1).truncationMode(.tail)
                }
                Text(notes).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 6)
            if build.isInstalled {
                Text("Installed").font(.caption).foregroundStyle(.secondary)
                Button("Reinstall") { install(build) }.controlSize(.small).disabled(busy)
            } else {
                Button("Install") { install(build) }.buttonStyle(.borderedProminent).controlSize(.small).disabled(busy)
            }
        }
        .padding(.horizontal, 8).padding(.vertical, 5)
        .help(build.products.joined(separator: ", ") + "\n\(build.source.path)\nBuild \(build.build_id) · \(build.key)"
              + " · \(build.configuration)\nInstalled from this build: " + (build.installed.isEmpty ? "none" : build.installed.joined(separator: ", ")))
        .contextMenu {
            Button("Reveal checkout in Finder") { NSWorkspace.shared.activateFileViewerSelecting([build.source.root]) }
        }
    }
}
