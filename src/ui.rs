use std::cell::{Cell, RefCell};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::mpsc;

use gtk::gio;
use gtk::glib;
use gtk::prelude::*;
use gtk::{Application, ApplicationWindow, Builder, Button, Entry, TextView};

use crate::config::Settings;
use crate::downloads::{self, DownloadWatcher, UsedDownloads};
use crate::importer::{self, ImportRequest};
use crate::lcsc;
use crate::models::{Match, ProviderState, ProviderStatus, SearchOutcome, SearchResult};
use crate::ranking::{rank, warnings};
use crate::schlib;
use crate::sources;

const SITES: [(&str, &str); 9] = [
    ("Octopart", "https://octopart.com/search?q="),
    ("SnapMagic", "https://www.snapeda.com/search/?q="),
    (
        "Ultra Librarian",
        "https://app.ultralibrarian.com/search?queryText=",
    ),
    (
        "CSE (SamacSys)",
        "https://componentsearchengine.com/search?term=",
    ),
    ("LCSC", "https://www.lcsc.com/search?q="),
    (
        "JLCPCB / EasyEDA",
        "https://jlcpcb.com/parts/componentSearch?searchTxt=",
    ),
    ("Mouser (web)", "https://www.mouser.com/c/?q="),
    (
        "DigiKey (web)",
        "https://www.digikey.com/en/products/result?keywords=",
    ),
    ("Celestial Library", "https://altiumlibrary.com/GetStarted"),
];

#[derive(Clone)]
struct ResultWidgets {
    view: gtk::ColumnView,
    selection: gtk::SingleSelection,
    store: gio::ListStore,
    manufacturer: Entry,
    part_number: Entry,
    description: Entry,
    datasheet: Entry,
    lcsc: Entry,
    easyeda: Entry,
    source_button: Button,
    datasheet_button: Button,
    messages: TextView,
    status: gtk::Label,
    search_info: gtk::Label,
}

#[derive(Clone)]
struct StepModel {
    path: PathBuf,
    placement: Option<[f64; 6]>,
}

pub fn run() {
    let application = Application::builder()
        .application_id("com.pallobgain.AltiumLibraryManager")
        .build();
    application.connect_activate(build_window);
    application.run();
}

fn object<T: glib::object::IsA<glib::Object> + Clone + 'static>(builder: &Builder, id: &str) -> T {
    builder
        .object(id)
        .unwrap_or_else(|| panic!("Missing UI object: {id}"))
}

fn build_window(app: &Application) {
    let builder = Builder::from_string(include_str!("../resources/app.ui"));
    let window: ApplicationWindow = object(&builder, "main_window");
    window.set_application(Some(app));

    let css = gtk::CssProvider::new();
    css.load_from_string(include_str!("../resources/style.css"));
    gtk::style_context_add_provider_for_display(
        &gtk::prelude::WidgetExt::display(&window),
        &css,
        gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
    );

    let mpn: Entry = object(&builder, "mpn_entry");
    let search: Button = object(&builder, "search_button");
    let settings = Rc::new(RefCell::new(Settings::load()));
    let source_sch: Entry = object(&builder, "source_schlib_entry");
    let source_pcb: Entry = object(&builder, "source_pcblib_entry");
    let source_generation = Rc::new(Cell::new(0u64));
    let source_step = Rc::new(RefCell::new(None::<StepModel>));
    for entry in [&source_sch, &source_pcb] {
        let generation = source_generation.clone();
        let step = source_step.clone();
        entry.connect_changed(move |_| {
            generation.set(generation.get().wrapping_add(1));
            *step.borrow_mut() = None;
        });
    }
    mpn.set_text(&settings.borrow().get_string("last_mpn"));

    let store = gio::ListStore::new::<glib::BoxedAnyObject>();
    let selection = gtk::SingleSelection::new(Some(store.clone()));
    selection.set_autoselect(false);
    selection.set_can_unselect(true);
    let widgets = ResultWidgets {
        view: object(&builder, "results_view"),
        selection,
        store,
        manufacturer: object(&builder, "manufacturer_entry"),
        part_number: object(&builder, "part_number_entry"),
        description: object(&builder, "description_entry"),
        datasheet: object(&builder, "datasheet_entry"),
        lcsc: object(&builder, "lcsc_entry"),
        easyeda: object(&builder, "easyeda_entry"),
        source_button: object(&builder, "source_button"),
        datasheet_button: object(&builder, "datasheet_button"),
        messages: object(&builder, "messages"),
        status: object(&builder, "status_label"),
        search_info: object(&builder, "search_info"),
    };
    widgets.view.set_model(Some(&widgets.selection));
    setup_columns(&widgets.view);
    widgets.messages.buffer().set_text(
        "Enter a part number and search, or open a library site to find a model manually.",
    );

    {
        let selector: gtk::DropDown = object(&builder, "component_selector");
        let generation = Rc::new(Cell::new(0u64));
        let status = widgets.status.clone();
        let messages = widgets.messages.clone();
        let lcsc = widgets.lcsc.clone();
        let mpn = mpn.clone();
        source_sch.connect_changed(move |entry| {
            let token = generation.get().wrapping_add(1);
            generation.set(token);
            selector.set_model(None::<&gtk::StringList>);
            selector.set_sensitive(false);
            let path = entry.text().to_string();
            if path.is_empty() {
                return;
            }
            let (sender, receiver) = mpsc::channel();
            std::thread::spawn(move || {
                let _ = sender.send(schlib::list_symbols(Path::new(&path)));
            });
            let generation = generation.clone();
            let selector = selector.clone();
            let status = status.clone();
            let messages = messages.clone();
            let mpn = mpn.clone();
            let lcsc = lcsc.clone();
            glib::timeout_add_local(std::time::Duration::from_millis(100), move || {
                if generation.get() != token {
                    return glib::ControlFlow::Break;
                }
                match receiver.try_recv() {
                    Ok(Ok(symbols)) => {
                        if symbols.is_empty() {
                            status.set_text("No schematic components found in the source library.");
                        } else {
                            let list = gtk::StringList::new(&["Select a component"]);
                            for symbol in &symbols {
                                list.append(&symbol.name);
                            }
                            let matched = schlib::best_match(
                                &symbols,
                                &[mpn.text().as_str(), lcsc.text().as_str()],
                            );
                            let selected = if symbols.len() == 1 {
                                1
                            } else {
                                matched
                                    .and_then(|name| symbols.iter().position(|s| s.name == name))
                                    .map_or(0, |index| index as u32 + 1)
                            };
                            selector.set_model(Some(&list));
                            selector.set_selected(selected);
                            selector.set_sensitive(true);
                            status.set_text("Select a component to import.");
                        }
                        glib::ControlFlow::Break
                    }
                    Ok(Err(error)) => {
                        status.set_text("Could not read schematic components.");
                        messages.buffer().set_text(&error);
                        glib::ControlFlow::Break
                    }
                    Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                    Err(mpsc::TryRecvError::Disconnected) => {
                        status.set_text("Component reader stopped unexpectedly.");
                        glib::ControlFlow::Break
                    }
                }
            });
        });
    }

    for (entry_id, button_id, title, suffix, setting) in [
        (
            "schlib_entry",
            "schlib_button",
            "Select destination SchLib",
            "SchLib",
            "schlib",
        ),
        (
            "pcblib_entry",
            "pcblib_button",
            "Select destination PcbLib",
            "PcbLib",
            "pcblib",
        ),
    ] {
        let entry: Entry = object(&builder, entry_id);
        let button: Button = object(&builder, button_id);
        entry.set_text(&settings.borrow().get_string(setting));
        let window = window.clone();
        let settings = settings.clone();
        button.connect_clicked(move |_| {
            let dialog = gtk::FileDialog::builder().title(title).build();
            let filter = gtk::FileFilter::new();
            filter.set_name(Some(&format!("{suffix} libraries")));
            filter.add_suffix(suffix);
            dialog.set_default_filter(Some(&filter));
            let entry = entry.clone();
            let settings = settings.clone();
            dialog.open(
                Some(&window),
                gio::Cancellable::NONE,
                move |result| match result {
                    Ok(file) => {
                        if let Some(path) = file.path() {
                            let path = path.to_string_lossy().into_owned();
                            entry.set_text(&path);
                            settings.borrow_mut().set(setting, path);
                        }
                    }
                    Err(error) if error.matches(gtk::DialogError::Dismissed) => {}
                    Err(error) => eprintln!("Could not select library: {error}"),
                },
            );
        });
    }

    let sites: gtk::Grid = object(&builder, "sites_grid");
    for (index, (name, base)) in SITES.iter().enumerate() {
        let button = Button::with_label(name);
        let mpn = mpn.clone();
        let status = widgets.status.clone();
        button.connect_clicked(move |_| {
            let query = mpn.text();
            if query.trim().is_empty() && *base != "https://altiumlibrary.com/GetStarted" {
                status.set_text("Enter a part number before opening a library search.");
                return;
            }
            open_url(&site_url(base, query.trim()), &status);
        });
        sites.attach(&button, (index % 2) as i32, (index / 2) as i32, 1, 1);
    }
    let all_sites = Button::with_label("Open ALL sites");
    {
        let mpn = mpn.clone();
        let status = widgets.status.clone();
        all_sites.connect_clicked(move |_| {
            let query = mpn.text();
            if query.trim().is_empty() {
                status.set_text("Enter a part number before opening library searches.");
                return;
            }
            for (_, base) in SITES {
                if let Err(error) = launch_url(&site_url(base, query.trim())) {
                    status.set_text(&format!("Could not open all library sites: {error}"));
                    return;
                }
            }
            status.set_text("Opened library sites in your browser.");
        });
    }
    sites.attach(&all_sites, 1, 4, 1, 1);
    let altium_search = Button::with_label("Altium Manufacturer Part Search");
    {
        let mpn = mpn.clone();
        let window = window.clone();
        let widgets = widgets.clone();
        altium_search.connect_clicked(move |_| {
            let query = mpn.text();
            if query.trim().is_empty() {
                widgets.status.set_text("Enter a manufacturer part number.");
                return;
            }
            gtk::prelude::WidgetExt::display(&window).clipboard().set_text(query.trim());
            widgets.messages.buffer().set_text(
                "Part number copied. In Altium Designer, open View > Panels > Manufacturer Part Search and paste it.",
            );
            widgets.status.set_text("Part number copied for Altium Manufacturer Part Search.");
        });
    }
    sites.attach(&altium_search, 0, 5, 2, 1);
    {
        let button: Button = object(&builder, "ul_browser_button");
        let mpn = mpn.clone();
        let status = widgets.status.clone();
        button.connect_clicked(move |_| {
            let query = mpn.text();
            if query.trim().is_empty() {
                status.set_text("Enter a part number before opening Ultra Librarian.");
                return;
            }
            open_url(&site_url(SITES[2].1, query.trim()), &status);
        });
    }

    for (id, suffix, entry) in [
        ("source_schlib_button", Some("SchLib"), source_sch.clone()),
        ("source_pcblib_button", Some("PcbLib"), source_pcb.clone()),
    ] {
        let button: Button = object(&builder, id);
        let window = window.clone();
        let status = widgets.status.clone();
        button.connect_clicked(move |_| {
            choose_source(&window, suffix, entry.clone(), status.clone());
        });
    }
    {
        let button: Button = object(&builder, "pick_source_button");
        let window = window.clone();
        let sch = source_sch.clone();
        let pcb = source_pcb.clone();
        let status = widgets.status.clone();
        let messages = widgets.messages.clone();
        let generation = source_generation.clone();
        let step = source_step.clone();
        let settings = settings.clone();
        button.connect_clicked(move |_| {
            let dialog = gtk::FileDialog::builder()
                .title("Select a source library")
                .build();
            let filter = gtk::FileFilter::new();
            filter.set_name(Some("Altium libraries and ZIP packages"));
            for suffix in ["zip", "SchLib", "PcbLib", "IntLib", "LibPkg"] {
                filter.add_suffix(suffix);
            }
            dialog.set_default_filter(Some(&filter));
            let sch = sch.clone();
            let pcb = pcb.clone();
            let status = status.clone();
            let messages = messages.clone();
            let generation = generation.clone();
            let step = step.clone();
            let x2_path = settings.borrow().get_string("x2_path");
            dialog.open(
                Some(&window),
                gio::Cancellable::NONE,
                move |result| match result {
                    Ok(file) => match file.path() {
                        Some(path) => load_source(
                            path,
                            sch.clone(),
                            pcb.clone(),
                            status.clone(),
                            messages.clone(),
                            generation.clone(),
                            step.clone(),
                            x2_path.clone(),
                            None,
                        ),
                        None => status.set_text("Selected file is not a local file."),
                    },
                    Err(error) if error.matches(gtk::DialogError::Dismissed) => {}
                    Err(error) => {
                        status.set_text(&format!("Could not select source library: {error}"))
                    }
                },
            );
        });
    }
    {
        let button: Button = object(&builder, "pick_folder_button");
        let window = window.clone();
        let sch = source_sch.clone();
        let pcb = source_pcb.clone();
        let status = widgets.status.clone();
        let messages = widgets.messages.clone();
        let generation = source_generation.clone();
        let step = source_step.clone();
        let settings = settings.clone();
        button.connect_clicked(move |_| {
            let dialog = gtk::FileDialog::builder()
                .title("Select a folder containing libraries")
                .build();
            let sch = sch.clone();
            let pcb = pcb.clone();
            let status = status.clone();
            let messages = messages.clone();
            let generation = generation.clone();
            let step = step.clone();
            let x2_path = settings.borrow().get_string("x2_path");
            dialog.select_folder(
                Some(&window),
                gio::Cancellable::NONE,
                move |result| match result {
                    Ok(folder) => match folder.path() {
                        Some(path) => load_source(
                            path,
                            sch.clone(),
                            pcb.clone(),
                            status.clone(),
                            messages.clone(),
                            generation.clone(),
                            step.clone(),
                            x2_path.clone(),
                            None,
                        ),
                        None => status.set_text("Selected folder is not local."),
                    },
                    Err(error) if error.matches(gtk::DialogError::Dismissed) => {}
                    Err(error) => status.set_text(&format!("Could not select folder: {error}")),
                },
            );
        });
    }

    {
        let button: Button = object(&builder, "watch_button");
        let watcher = Rc::new(RefCell::new(None::<DownloadWatcher>));
        let watch_session = Rc::new(Cell::new(0u64));
        let used = Rc::new(RefCell::new(UsedDownloads::default()));
        let mpn = mpn.clone();
        let settings = settings.clone();
        let status = widgets.status.clone();
        let messages = widgets.messages.clone();
        let sch = source_sch.clone();
        let pcb = source_pcb.clone();
        let generation = source_generation.clone();
        let watch_generation = source_generation.clone();
        let step = source_step.clone();
        button.connect_clicked(move |button| {
            let session = watch_session.get().wrapping_add(1);
            watch_session.set(session);
            if watcher.borrow().is_some() {
                *watcher.borrow_mut() = None;
                button.set_label("Look for download");
                status.set_text("Stopped waiting for a download.");
                return;
            }
            let configured = settings.borrow().get_string("cse_watch_dir");
            let folder = if configured.is_empty() {
                match dirs::download_dir() {
                    Some(path) => path,
                    None => {
                        status.set_text("Could not locate the Downloads folder.");
                        return;
                    }
                }
            } else {
                PathBuf::from(configured)
            };
            let watch = match DownloadWatcher::new(&folder, mpn.text().as_str()) {
                Ok(watch) => watch,
                Err(error) => {
                    status.set_text(&error);
                    return;
                }
            };
            let button = button.clone();
            let status = status.clone();
            let messages = messages.clone();
            let sch = sch.clone();
            let pcb = pcb.clone();
            let generation = generation.clone();
            let step = step.clone();
            let settings = settings.clone();
            let used = used.clone();
            let handle_used = used.clone();
            let handle_status = status.clone();
            let handle = Rc::new(move |path: PathBuf| {
                handle_status.set_text("Copying the downloaded library...");
                let token = generation.get();
                let (sender, receiver) = mpsc::channel();
                std::thread::spawn(move || {
                    let _ = sender.send(downloads::claim(&path, &crate::config::app_dir()));
                });
                let used = handle_used.clone();
                let status = handle_status.clone();
                let messages = messages.clone();
                let generation = generation.clone();
                let sch = sch.clone();
                let pcb = pcb.clone();
                let step = step.clone();
                let settings = settings.clone();
                glib::timeout_add_local(std::time::Duration::from_millis(100), move || {
                    match receiver.try_recv() {
                        Ok(result) => {
                            match result {
                                Ok(claim) => {
                                    used.borrow_mut().record(&claim);
                                    if generation.get() == token {
                                        load_source(
                                            claim.path,
                                            sch.clone(),
                                            pcb.clone(),
                                            status.clone(),
                                            messages.clone(),
                                            generation.clone(),
                                            step.clone(),
                                            settings.borrow().get_string("x2_path"),
                                            None,
                                        );
                                    } else {
                                        status.set_text(
                                            "The source changed; the download was not selected.",
                                        );
                                    }
                                }
                                Err(error) => {
                                    status.set_text("Could not load the download.");
                                    messages.buffer().set_text(&error);
                                }
                            }
                            glib::ControlFlow::Break
                        }
                        Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                        Err(mpsc::TryRecvError::Disconnected) => {
                            status.set_text("Download copy worker stopped unexpectedly.");
                            glib::ControlFlow::Break
                        }
                    }
                });
            });
            let recent = watch.recent(&used.borrow());
            match recent {
                Ok(Some(path)) => {
                    handle(path);
                    return;
                }
                Err(error) => {
                    status.set_text(&error);
                    return;
                }
                Ok(None) => {}
            }
            *watcher.borrow_mut() = Some(watch);
            button.set_label("Stop waiting");
            status.set_text(&format!(
                "Waiting for a library download in {}.",
                folder.display()
            ));
            let watcher = watcher.clone();
            let watch_session = watch_session.clone();
            let generation = watch_generation.clone();
            let source_token = generation.get();
            glib::timeout_add_local(std::time::Duration::from_secs(1), move || {
                if watch_session.get() != session {
                    return glib::ControlFlow::Break;
                }
                if generation.get() != source_token {
                    *watcher.borrow_mut() = None;
                    button.set_label("Look for download");
                    return glib::ControlFlow::Break;
                }
                let result = {
                    let mut watch = watcher.borrow_mut();
                    match watch.as_mut() {
                        Some(watch) => watch.poll(&used.borrow()),
                        None => return glib::ControlFlow::Break,
                    }
                };
                match result {
                    Ok(None) => glib::ControlFlow::Continue,
                    Ok(Some(path)) => {
                        *watcher.borrow_mut() = None;
                        button.set_label("Look for download");
                        handle(path);
                        glib::ControlFlow::Break
                    }
                    Err(error) => {
                        *watcher.borrow_mut() = None;
                        button.set_label("Look for download");
                        status.set_text(&error);
                        glib::ControlFlow::Break
                    }
                }
            });
        });
    }

    for (button_id, provider) in [("cse_button", "CSE"), ("snapeda_button", "SnapEDA")] {
        let button: Button = object(&builder, button_id);
        let window = window.clone();
        let mpn = mpn.clone();
        let manufacturer = widgets.manufacturer.clone();
        let status = widgets.status.clone();
        let messages = widgets.messages.clone();
        let sch = source_sch.clone();
        let pcb = source_pcb.clone();
        let generation = source_generation.clone();
        let step = source_step.clone();
        let settings = settings.clone();
        button.connect_clicked(move |button| {
            let query = mpn.text().trim().to_owned();
            if query.is_empty() {
                status.set_text(&format!("Enter a part number before downloading from {provider}."));
                return;
            }
            let maker = manufacturer.text().trim().to_owned();
            let dialog = gtk::Window::builder()
                .title(if provider == "CSE" { "Component Search Engine" } else { "SnapEDA" })
                .transient_for(&window)
                .modal(true)
                .build();
            let area = gtk::Box::new(gtk::Orientation::Vertical, 8);
            area.set_margin_top(12);
            area.set_margin_bottom(12);
            area.set_margin_start(12);
            area.set_margin_end(12);
            let info = gtk::Label::new(Some(&format!("Enter your {provider} login. It is used only for this download and is not saved.")));
            info.set_wrap(true);
            area.append(&info);
            let email = Entry::builder().placeholder_text(format!("{provider} username")).build();
            let password = gtk::PasswordEntry::builder().placeholder_text(format!("{provider} password")).show_peek_icon(true).build();
            area.append(&email);
            area.append(&password);
            let actions = gtk::Box::new(gtk::Orientation::Horizontal, 8);
            actions.set_halign(gtk::Align::End);
            let cancel = Button::with_label("Cancel");
            let download = Button::with_label("Download");
            actions.append(&cancel);
            actions.append(&download);
            area.append(&actions);
            dialog.set_child(Some(&area));
            let weak_dialog = dialog.downgrade();
            cancel.connect_clicked(move |_| {
                if let Some(dialog) = weak_dialog.upgrade() {
                    dialog.close();
                }
            });
            let button = button.clone();
            let status = status.clone();
            let messages = messages.clone();
            let sch = sch.clone();
            let pcb = pcb.clone();
            let generation = generation.clone();
            let step = step.clone();
            let settings = settings.clone();
            let mpn = mpn.clone();
            let manufacturer = manufacturer.clone();
            let weak_dialog = dialog.downgrade();
            download.connect_clicked(move |_| {
                let email = email.text().to_string();
                let password = password.text().to_string();
                if let Some(dialog) = weak_dialog.upgrade() {
                    dialog.close();
                }
                if email.is_empty() || password.is_empty() {
                    status.set_text(&format!("{provider} username and password are required."));
                    return;
                }
                button.set_sensitive(false);
                status.set_text(&format!("Searching and downloading from {provider}..."));
                let token = generation.get();
                let query_for_thread = query.clone();
                let maker_for_thread = maker.clone();
                let (sender, receiver) = mpsc::channel();
                std::thread::spawn(move || {
                    let result = if provider == "CSE" {
                        crate::cse::download(&query_for_thread, &maker_for_thread, &email, &password, &crate::config::app_dir())
                            .map(|result| (result.path, Some((result.placement, result.warning))))
                    } else {
                        crate::snapeda::download(&query_for_thread, &maker_for_thread, &email, &password, &crate::config::app_dir())
                            .map(|path| (path, None))
                    };
                    let _ = sender.send(result);
                });
                let button = button.clone();
                let status = status.clone();
                let messages = messages.clone();
                let sch = sch.clone();
                let pcb = pcb.clone();
                let generation = generation.clone();
                let step = step.clone();
                let settings = settings.clone();
                let mpn = mpn.clone();
                let manufacturer = manufacturer.clone();
                let query = query.clone();
                let maker = maker.clone();
                glib::timeout_add_local(std::time::Duration::from_millis(100), move || match receiver.try_recv() {
                    Ok(result) => {
                        button.set_sensitive(true);
                        if token != generation.get() || mpn.text().trim() != query || manufacturer.text().trim() != maker {
                            status.set_text(&format!("The source, part number, or manufacturer changed; the {provider} download was not selected."));
                        } else {
                            match result {
                                Ok((path, placement)) => load_source(path, sch.clone(), pcb.clone(), status.clone(), messages.clone(), generation.clone(), step.clone(), settings.borrow().get_string("x2_path"), placement),
                                Err(error) => {
                                    status.set_text(&format!("{provider} download failed."));
                                    messages.buffer().set_text(&error);
                                }
                            }
                        }
                        glib::ControlFlow::Break
                    }
                    Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                    Err(mpsc::TryRecvError::Disconnected) => {
                        button.set_sensitive(true);
                        status.set_text(&format!("{provider} download worker stopped unexpectedly."));
                        glib::ControlFlow::Break
                    }
                });
            });
            dialog.present();
        });
    }

    {
        let button: Button = object(&builder, "easyeda_button");
        let entry = widgets.easyeda.clone();
        let status = widgets.status.clone();
        let messages = widgets.messages.clone();
        let sch = source_sch.clone();
        let pcb = source_pcb.clone();
        let generation = source_generation.clone();
        let settings = settings.clone();
        let mpn = mpn.clone();
        let manufacturer = widgets.manufacturer.clone();
        let part_number = widgets.part_number.clone();
        let description = widgets.description.clone();
        let lcsc_param = widgets.lcsc.clone();
        let import_button: Button = object(&builder, "import_button");
        let activate_button = button.clone();
        entry.connect_activate(move |_| {
            if activate_button.is_sensitive() {
                activate_button.emit_clicked();
            }
        });
        button.connect_clicked(move |button| {
            if !import_button.is_sensitive() {
                status.set_text("Wait for the current Altium import or resolve its error before fetching EasyEDA data.");
                return;
            }
            let input = if entry.text().trim().is_empty() {
                lcsc_param.text().to_string()
            } else {
                entry.text().to_string()
            };
            let lcsc = match crate::easyeda::clean_lcsc(&input) {
                Ok(lcsc) => lcsc,
                Err(error) => {
                    status.set_text(&error);
                    return;
                }
            };
            let Some(x2) = crate::altium::find_x2(&settings.borrow().get_string("x2_path")) else {
                status.set_text("Altium Designer was not found. Set x2_path in settings.");
                return;
            };
            entry.set_text(&lcsc);
            let token = generation.get();
            let expected_mpn = mpn.text().to_string();
            let expected_manufacturer = manufacturer.text().to_string();
            button.set_sensitive(false);
            import_button.set_sensitive(false);
            status.set_text(&format!("Fetching {lcsc} from EasyEDA and building staging libraries in Altium..."));
            messages.buffer().set_text(crate::easyeda::UNOFFICIAL_NOTE);
            let (sender, receiver) = mpsc::channel();
            let request_lcsc = lcsc.clone();
            std::thread::spawn(move || {
                let result = crate::easyeda::fetch_and_build(&request_lcsc, &x2, &crate::config::app_dir());
                let _ = sender.send(result);
            });
            let button = button.clone();
            let entry = entry.clone();
            let status = status.clone();
            let messages = messages.clone();
            let sch = sch.clone();
            let pcb = pcb.clone();
            let generation = generation.clone();
            let mpn = mpn.clone();
            let manufacturer = manufacturer.clone();
            let part_number = part_number.clone();
            let description = description.clone();
            let lcsc_param = lcsc_param.clone();
            let import_button = import_button.clone();
            glib::timeout_add_local(std::time::Duration::from_millis(100), move || match receiver.try_recv() {
                Ok(result) => {
                    let needs_attention = result.as_ref().err().is_some_and(|error| error.requires_intervention);
                    button.set_sensitive(!needs_attention);
                    import_button.set_sensitive(!needs_attention);
                    if let Err(error) = &result {
                        if error.requires_intervention {
                            status.set_text("EasyEDA build result is unknown; check Altium before retrying or importing.");
                            messages.buffer().set_text(&format!("{}\n{}\nAltium may still be running the staging script. After checking Altium, restart the app to retry or import.", crate::easyeda::UNOFFICIAL_NOTE, error.message));
                            return glib::ControlFlow::Break;
                        }
                    }
                    if generation.get() != token || entry.text().as_str() != lcsc || mpn.text().as_str() != expected_mpn || manufacturer.text().as_str() != expected_manufacturer {
                        status.set_text("The source, part number, manufacturer, or LCSC number changed; the EasyEDA result was not selected.");
                    } else {
                        match result {
                            Ok(staged) => {
                                lcsc_param.set_text(&staged.lcsc);
                                for (field, value) in [
                                    (&manufacturer, staged.part.params.get("Manufacturer")),
                                    (&part_number, staged.part.params.get("Manufacturer Part")),
                                    (&description, staged.part.meta.get("DESC")),
                                ] {
                                    if field.text().trim().is_empty() {
                                        if let Some(value) = value.filter(|v| !v.is_empty()) { field.set_text(value); }
                                    }
                                }
                                pcb.set_text(&staged.pcblib.to_string_lossy());
                                sch.set_text(&staged.schlib.to_string_lossy());
                                let mut report = vec![crate::easyeda::UNOFFICIAL_NOTE.to_owned()];
                                report.extend(staged.part.warnings.iter().map(|w| format!("Converter: {w}")));
                                report.extend(staged.build.warnings.iter().map(|w| format!("Builder: {w}")));
                                report.push(format!("Built symbol {} ({} pins) and footprint {} ({} pads, {} objects) in staging libraries. Choose the destination and click Import into Altium.", staged.build.symbol, staged.part.pins, staged.build.footprint, staged.part.pads, staged.build.primitives.unwrap_or(0)));
                                messages.buffer().set_text(&report.join("\n"));
                                status.set_text(&format!("{} ready to import.", staged.lcsc));
                            }
                            Err(error) => {
                                status.set_text("EasyEDA part could not be built.");
                                messages.buffer().set_text(&format!("{}\n{}", crate::easyeda::UNOFFICIAL_NOTE, error.message));
                            }
                        }
                    }
                    glib::ControlFlow::Break
                }
                Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                Err(mpsc::TryRecvError::Disconnected) => {
                    status.set_text("EasyEDA worker stopped unexpectedly; check Altium, then restart the app before retrying or importing.");
                    glib::ControlFlow::Break
                }
            });
        });
    }

    {
        let button: Button = object(&builder, "import_button");
        let selector: gtk::DropDown = object(&builder, "component_selector");
        let link: gtk::DropDown = object(&builder, "link_mode");
        let rename: gtk::CheckButton = object(&builder, "conflict_rename");
        let dest_sch: Entry = object(&builder, "schlib_entry");
        let dest_pcb: Entry = object(&builder, "pcblib_entry");
        let status = widgets.status.clone();
        let messages = widgets.messages.clone();
        let manufacturer = widgets.manufacturer.clone();
        let part_number = widgets.part_number.clone();
        let description = widgets.description.clone();
        let datasheet = widgets.datasheet.clone();
        let lcsc = widgets.lcsc.clone();
        let settings = settings.clone();
        let button_handle = button.clone();
        let source_step = source_step.clone();
        let window = window.clone();
        button.connect_clicked(move |_| {
            if selector.selected() == 0 || !selector.is_sensitive() {
                status.set_text("Select a schematic component first.");
                return;
            }
            let Some(component) = selector.selected_item().and_downcast::<gtk::StringObject>() else {
                status.set_text("Select a schematic component first.");
                return;
            };
            let Some(mode) = ["name", "path", "any"].get(link.selected() as usize) else {
                status.set_text("Select a footprint link mode.");
                return;
            };
            let Some(x2) = crate::altium::find_x2(&settings.borrow().get_string("x2_path")) else {
                status.set_text("Altium Designer executable not found.");
                messages.buffer().set_text("Could not locate X2.EXE. Install Altium in the default location or configure x2_path in settings.json.");
                return;
            };
            let req = ImportRequest {
                src_schlib: PathBuf::from(source_sch.text().as_str()),
                src_pcblib: PathBuf::from(source_pcb.text().as_str()),
                dest_schlib: PathBuf::from(dest_sch.text().as_str()),
                dest_pcblib: PathBuf::from(dest_pcb.text().as_str()),
                component: component.string().to_string(),
                params: std::collections::HashMap::from([
                    ("Manufacturer".into(), manufacturer.text().to_string()),
                    ("Manufacturer Part Number".into(), part_number.text().to_string()),
                    ("Datasheet".into(), datasheet.text().to_string()),
                    ("LCSC Part #".into(), lcsc.text().to_string()),
                ]),
                description: description.text().to_string(),
                conflict: if rename.is_active() { "rename" } else { "skip" }.into(),
                link_mode: (*mode).into(),
                step_file: source_step.borrow().as_ref().map(|s| s.path.clone()),
                step_placement: source_step.borrow().as_ref().and_then(|s| s.placement),
            };
            button_handle.set_sensitive(false);
            let dialog = gtk::AlertDialog::builder()
                .message("Import into Altium?")
                .detail(format!(
                    "This Rust import has not yet been verified end-to-end with Altium. Use copies of your libraries first.\n\nDestination SchLib: {}\nDestination PcbLib: {}",
                    req.dest_schlib.display(), req.dest_pcblib.display()
                ))
                .buttons(["Cancel", "Import"])
                .default_button(0)
                .cancel_button(0)
                .modal(true)
                .build();
            let button = button_handle.clone();
            let status = status.clone();
            let messages = messages.clone();
            dialog.choose(Some(&window), gio::Cancellable::NONE, move |choice| {
                match choice {
                    Ok(1) => start_import(req, x2, button, status, messages),
                    Ok(_) => button.set_sensitive(true),
                    Err(error) if error.matches(gtk::DialogError::Dismissed) => button.set_sensitive(true),
                    Err(error) => {
                        status.set_text(&format!("Could not confirm import: {error}"));
                        button.set_sensitive(true);
                    }
                }
            });
        });
    }

    fn start_import(
        req: ImportRequest,
        x2: PathBuf,
        button: Button,
        status: gtk::Label,
        messages: TextView,
    ) {
        status.set_text("Importing in Altium; do not edit the destination libraries.");
        messages
            .buffer()
            .set_text("Preparing Altium and backing up destination libraries...");
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let work_dir = crate::config::app_dir();
            let mut report = importer::run_import(&req, &x2, &work_dir);
            if report.is_ok() && !report.added_symbols.is_empty() {
                if let Err(error) = crate::db::Database::open(work_dir.join("alm.db"))
                    .and_then(|db| db.record_import(&req, &report))
                {
                    report.warnings.push(format!(
                        "Import was saved but could not be recorded in the local database: {error}"
                    ));
                }
            }
            let _ = sender.send(report);
        });
        glib::timeout_add_local(
            std::time::Duration::from_millis(100),
            move || match receiver.try_recv() {
                Ok(report) => {
                    status.set_text(if report.is_ok() {
                        "Import finished. Review Messages."
                    } else {
                        "Import failed or its result is unknown. Review Messages."
                    });
                    messages.buffer().set_text(&report.summary());
                    button.set_sensitive(!report.requires_intervention);
                    glib::ControlFlow::Break
                }
                Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                Err(mpsc::TryRecvError::Disconnected) => {
                    status.set_text("Import worker stopped unexpectedly; check Altium and the backups before retrying.");
                    button.set_sensitive(false);
                    glib::ControlFlow::Break
                }
            },
        );
    }

    {
        let widgets = widgets.clone();
        widgets
            .selection
            .clone()
            .connect_selected_notify(move |_| update_selection(&widgets));
    }
    for (button, datasheet) in [
        (&widgets.source_button, false),
        (&widgets.datasheet_button, true),
    ] {
        let widgets = widgets.clone();
        button.connect_clicked(move |_| open_selected(&widgets, datasheet));
    }
    {
        let widgets = widgets.clone();
        widgets
            .view
            .clone()
            .connect_activate(move |_, _| open_selected(&widgets, false));
    }

    let search_button = search.clone();
    let search_entry = mpn.clone();
    let search_widgets = widgets.clone();
    search.connect_clicked(move |_| {
        let query = search_entry.text().trim().to_owned();
        if query.is_empty() {
            search_widgets
                .status
                .set_text("Enter a manufacturer part number.");
            return;
        }
        settings.borrow_mut().set("last_mpn", query.clone());
        search_widgets.store.remove_all();
        clear_parameters(&search_widgets);
        search_widgets
            .messages
            .buffer()
            .set_text("Searching LCSC...");
        search_widgets.search_info.set_text("Searching LCSC...");
        search_widgets
            .status
            .set_text(&format!("Searching '{query}'..."));
        search_button.set_sensitive(false);
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let _ = sender.send(do_search(&query));
        });
        let widgets = search_widgets.clone();
        let button = search_button.clone();
        let entry = search_entry.clone();
        glib::timeout_add_local(
            std::time::Duration::from_millis(100),
            move || match receiver.try_recv() {
                Ok(outcome) => {
                    if entry.text().trim() == outcome.query {
                        show_outcome(&widgets, outcome);
                    } else {
                        widgets
                            .status
                            .set_text("Part number changed. Search again for the new value.");
                        widgets
                            .search_info
                            .set_text("Search cancelled: part number changed.");
                    }
                    button.set_sensitive(true);
                    glib::ControlFlow::Break
                }
                Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                Err(mpsc::TryRecvError::Disconnected) => {
                    widgets
                        .status
                        .set_text("Search worker stopped unexpectedly.");
                    widgets.search_info.set_text("Search failed.");
                    button.set_sensitive(true);
                    glib::ControlFlow::Break
                }
            },
        );
    });
    mpn.connect_activate(move |_| search.emit_clicked());
    window.present();
}

fn site_url(base: &str, query: &str) -> String {
    if base == "https://altiumlibrary.com/GetStarted" {
        return base.to_owned();
    }
    let encoded = url::form_urlencoded::byte_serialize(query.as_bytes()).collect::<String>();
    format!("{base}{encoded}")
}

fn open_url(uri: &str, status: &gtk::Label) {
    if let Err(error) = launch_url(uri) {
        status.set_text(&error);
    }
}

fn launch_url(uri: &str) -> Result<(), String> {
    if !matches!(url::Url::parse(uri), Ok(ref url) if url.scheme() == "https" && url.host_str().is_some())
    {
        return Err("Cannot open an invalid or non-HTTPS link.".to_owned());
    }
    gio::AppInfo::launch_default_for_uri(uri, gio::AppLaunchContext::NONE)
        .map_err(|error| format!("Could not open link: {error}"))
}

fn library_type(path: &Path) -> Result<&'static str, String> {
    let extension = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or_default();
    let kind = if extension.eq_ignore_ascii_case("schlib") {
        "schlib"
    } else if extension.eq_ignore_ascii_case("pcblib") {
        "pcblib"
    } else {
        return Err("Select a .SchLib or .PcbLib file.".to_owned());
    };
    let mut file =
        std::fs::File::open(path).map_err(|e| format!("Cannot open {}: {e}", path.display()))?;
    let mut header = [0; 8];
    file.read_exact(&mut header)
        .map_err(|e| format!("Cannot read {}: {e}", path.display()))?;
    if header != [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1] {
        return Err(format!(
            "{} is not a binary Altium library.",
            path.display()
        ));
    }
    Ok(kind)
}

fn load_source(
    path: PathBuf,
    sch: Entry,
    pcb: Entry,
    status: gtk::Label,
    messages: TextView,
    generation: Rc<Cell<u64>>,
    step_file: Rc<RefCell<Option<StepModel>>>,
    x2_override: String,
    cse_result: Option<(Option<crate::cse::Placement>, Option<String>)>,
) {
    sch.set_text("");
    pcb.set_text("");
    let token = generation.get().wrapping_add(1);
    generation.set(token);
    status.set_text("Reading source package...");
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let result = sources::resolve_with_intlib(&path, &crate::config::app_dir(), &x2_override)
            .and_then(|package| {
                for file in [&package.schlib, &package.pcblib].into_iter().flatten() {
                    library_type(file)?;
                }
                Ok(package)
            });
        let _ = sender.send(result);
    });
    glib::timeout_add_local(
        std::time::Duration::from_millis(100),
        move || match receiver.try_recv() {
            Ok(result) => {
                if generation.get() != token {
                    return glib::ControlFlow::Break;
                }
                match result {
                    Ok(package) => {
                        if let Some(path) = &package.schlib {
                            sch.set_text(&path.to_string_lossy());
                        }
                        if let Some(path) = &package.pcblib {
                            pcb.set_text(&path.to_string_lossy());
                        }
                        let mut report = vec![
                            format!(
                                "Source SchLib: {}",
                                package.schlib.as_ref().map_or_else(
                                    || "NOT FOUND".to_owned(),
                                    |p| p.display().to_string()
                                )
                            ),
                            format!(
                                "Source PcbLib: {}",
                                package.pcblib.as_ref().map_or_else(
                                    || "NOT FOUND".to_owned(),
                                    |p| p.display().to_string()
                                )
                            ),
                        ];
                        if let Some((placement, warning)) = &cse_result {
                            if let Some(warning) = warning {
                                report.push(warning.clone());
                            }
                            if let Some(placement) = placement {
                                let matched: Vec<_> = package
                                    .step_files
                                    .iter()
                                    .filter(|p| {
                                        p.file_name().and_then(|n| n.to_str()).is_some_and(|n| {
                                            n.eq_ignore_ascii_case(&placement.filename)
                                        })
                                    })
                                    .collect();
                                if matched.len() == 1 {
                                    *step_file.borrow_mut() = Some(StepModel {
                                        path: matched[0].clone(),
                                        placement: Some(placement.values),
                                    });
                                    report.push(
                                        "CSE STEP placement will be applied; verify the 3D view."
                                            .to_owned(),
                                    );
                                } else {
                                    report.push("The STEP named by CSE was missing or ambiguous; no separate STEP will be attached.".to_owned());
                                }
                            }
                        } else if package.step_files.len() == 1 {
                            *step_file.borrow_mut() = Some(StepModel {
                                path: package.step_files[0].clone(),
                                placement: None,
                            });
                            report.push("One STEP file found; it will be attached at the origin if needed. Check placement in Altium.".to_owned());
                        } else if !package.step_files.is_empty() {
                            report.push(format!("Found {} STEP files; none will be attached automatically because the choice is ambiguous.", package.step_files.len()));
                        }
                        report.extend(package.notes);
                        messages.buffer().set_text(&report.join("\n"));
                        status.set_text("Source package read.");
                    }
                    Err(error) => {
                        status.set_text("Could not read source package.");
                        messages
                            .buffer()
                            .set_text(&format!("Cannot read source package: {error}"));
                    }
                }
                glib::ControlFlow::Break
            }
            Err(mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
            Err(mpsc::TryRecvError::Disconnected) => {
                if generation.get() == token {
                    status.set_text("Source package worker stopped unexpectedly.");
                }
                glib::ControlFlow::Break
            }
        },
    );
}

fn choose_source(
    window: &ApplicationWindow,
    suffix: Option<&'static str>,
    entry: Entry,
    status: gtk::Label,
) {
    let dialog = gtk::FileDialog::builder()
        .title("Select a source library")
        .build();
    if let Some(suffix) = suffix {
        let filter = gtk::FileFilter::new();
        filter.set_name(Some(&format!("{suffix} libraries")));
        filter.add_suffix(suffix);
        dialog.set_default_filter(Some(&filter));
    }
    dialog.open(
        Some(window),
        gio::Cancellable::NONE,
        move |result| match result {
            Ok(file) => match file.path() {
                Some(path) => match library_type(&path) {
                    Ok(kind)
                        if suffix.is_none_or(|expected| kind.eq_ignore_ascii_case(expected)) =>
                    {
                        entry.set_text(&path.to_string_lossy());
                        status.set_text("Source library selected.");
                    }
                    Ok(_) => status.set_text("Selected library has the wrong file type."),
                    Err(error) => status.set_text(&error),
                },
                None => status.set_text("Selected file is not a local file."),
            },
            Err(error) if error.matches(gtk::DialogError::Dismissed) => {}
            Err(error) => status.set_text(&format!("Could not select source library: {error}")),
        },
    );
}

fn setup_columns(view: &gtk::ColumnView) {
    for (index, title, width, expand) in [
        (0, "Match", 145, false),
        (1, "MPN", 155, true),
        (2, "Manufacturer", 150, true),
        (3, "Package", 100, false),
        (4, "LCSC stock", 90, false),
        (5, "Datasheet", 85, false),
        (6, "LCSC #", 90, false),
    ] {
        let factory = gtk::SignalListItemFactory::new();
        factory.connect_setup(|_, item| {
            let item = item.downcast_ref::<gtk::ListItem>().expect("list item");
            let label = gtk::Label::new(None);
            label.set_xalign(0.0);
            label.set_ellipsize(gtk::pango::EllipsizeMode::End);
            label.add_css_class("result-cell");
            item.set_child(Some(&label));
        });
        factory.connect_bind(move |_, item| {
            let item = item.downcast_ref::<gtk::ListItem>().expect("list item");
            let label = item
                .child()
                .and_downcast::<gtk::Label>()
                .expect("result label");
            let boxed = item
                .item()
                .and_downcast::<glib::BoxedAnyObject>()
                .expect("result");
            let result = boxed.borrow::<SearchResult>();
            label.remove_css_class("exact-row");
            label.remove_css_class("similar-row");
            label.add_css_class(if result.match_status == Match::Exact {
                "exact-row"
            } else {
                "similar-row"
            });
            let text = match index {
                0 => result.match_status.as_str().to_owned(),
                1 => result.mpn.clone(),
                2 => result.manufacturer.clone(),
                3 => result.package.clone(),
                4 => result
                    .stock
                    .map_or_else(|| "?".to_owned(), |stock| stock.to_string()),
                5 => if result.datasheet_url.is_empty() {
                    "No"
                } else {
                    "Yes"
                }
                .to_owned(),
                _ => result.lcsc.clone(),
            };
            label.set_label(&text);
            label.set_tooltip_text(Some(&text));
        });
        let column = gtk::ColumnViewColumn::new(Some(title), Some(factory));
        column.set_fixed_width(width);
        column.set_expand(expand);
        column.set_resizable(true);
        view.append_column(&column);
    }
}

fn selected_result(widgets: &ResultWidgets) -> Option<SearchResult> {
    widgets
        .selection
        .selected_item()
        .and_downcast::<glib::BoxedAnyObject>()
        .map(|boxed| boxed.borrow::<SearchResult>().clone())
}

fn clear_parameters(widgets: &ResultWidgets) {
    for entry in [
        &widgets.manufacturer,
        &widgets.part_number,
        &widgets.description,
        &widgets.datasheet,
        &widgets.lcsc,
        &widgets.easyeda,
    ] {
        entry.set_text("");
    }
    widgets.source_button.set_sensitive(false);
    widgets.datasheet_button.set_sensitive(false);
}

fn update_selection(widgets: &ResultWidgets) {
    let Some(result) = selected_result(widgets) else {
        clear_parameters(widgets);
        return;
    };
    widgets.manufacturer.set_text(&result.manufacturer);
    widgets.part_number.set_text(&result.mpn);
    widgets.description.set_text(&result.description);
    widgets.datasheet.set_text(&result.datasheet_url);
    widgets.lcsc.set_text(&result.lcsc);
    widgets.easyeda.set_text(&result.lcsc);
    widgets
        .source_button
        .set_sensitive(!result.source_url.is_empty());
    widgets
        .datasheet_button
        .set_sensitive(!result.datasheet_url.is_empty());
}

fn open_selected(widgets: &ResultWidgets, datasheet: bool) {
    if let Some(result) = selected_result(widgets) {
        let uri = if datasheet {
            &result.datasheet_url
        } else {
            &result.source_url
        };
        if !uri.is_empty() {
            open_url(uri, &widgets.status);
        }
    }
}

fn do_search(mpn: &str) -> SearchOutcome {
    let (results, status) = match lcsc::search(mpn) {
        Ok(parts) => {
            let results = parts
                .iter()
                .map(|part| SearchResult::from_lcsc(mpn, part))
                .collect::<Vec<_>>();
            let status = ProviderStatus {
                provider: "LCSC".to_owned(),
                state: ProviderState::Ok,
                count: results.len(),
                message: String::new(),
                url: "https://www.lcsc.com/".to_owned(),
            };
            (results, status)
        }
        Err(error) => (
            Vec::new(),
            ProviderStatus {
                provider: "LCSC".to_owned(),
                state: ProviderState::Error,
                count: 0,
                message: error.to_string(),
                url: "https://www.lcsc.com/".to_owned(),
            },
        ),
    };
    let searched = status.state == ProviderState::Ok;
    let ranked = rank(mpn, results);
    let warning_text = warnings(mpn, &ranked);
    let recommendation = if !searched {
        "LCSC could not be searched. Use the library site links or try again.".to_owned()
    } else if let Some(best) = ranked.iter().find(|r| r.match_status == Match::Exact) {
        format!("Recommended: {} by {} via LCSC. CAD availability is unknown; verify the model before use.",
            best.mpn, if best.manufacturer.is_empty() { "unknown manufacturer" } else { &best.manufacturer })
    } else {
        format!("No exact match for '{mpn}' from LCSC. Check the full part number or use the library site links.")
    };
    SearchOutcome {
        query: mpn.to_owned(),
        results: ranked,
        statuses: vec![status],
        recommendation,
        warnings: warning_text,
    }
}

fn show_outcome(widgets: &ResultWidgets, outcome: SearchOutcome) {
    let exact_count = outcome
        .results
        .iter()
        .filter(|r| r.match_status == Match::Exact)
        .count();
    let count = outcome.results.len();
    let failed = outcome
        .statuses
        .iter()
        .any(|status| status.state == ProviderState::Error);
    let mut message = outcome.recommendation.clone();
    for warning in &outcome.warnings {
        message.push_str(&format!("\nWarning: {warning}"));
    }
    for status in &outcome.statuses {
        if status.state == ProviderState::Error {
            message.push_str(&format!("\n{}: {}", status.provider, status.message));
        }
    }
    widgets.messages.buffer().set_text(&message);
    for result in outcome.results {
        widgets.store.append(&glib::BoxedAnyObject::new(result));
    }
    if exact_count > 0 {
        widgets.selection.set_selected(0);
    }
    widgets.search_info.set_text(if failed {
        "LCSC: search failed | Library sites: not searched"
    } else {
        "LCSC: searched | Library sites: not searched"
    });
    let status_text = if failed {
        "Search failed. See Messages.".to_owned()
    } else {
        format!("{count} result(s), {exact_count} exact.")
    };
    widgets.status.set_text(&status_text);
}

#[cfg(test)]
mod tests {
    use super::{library_type, site_url, sources};
    use std::path::Path;

    #[test]
    fn library_search_url_encodes_part_number() {
        assert_eq!(
            site_url("https://example.com/?q=", "A/B + C"),
            "https://example.com/?q=A%2FB+%2B+C"
        );
        assert_eq!(
            site_url("https://altiumlibrary.com/GetStarted", "A/B"),
            "https://altiumlibrary.com/GetStarted"
        );
    }

    #[test]
    fn source_library_validation_checks_extension_and_header() {
        let fixture = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("fixtures")
            .join("ThreeSymbols.SchLib");
        assert_eq!(library_type(&fixture).unwrap(), "schlib");
        assert!(library_type(Path::new("unsupported.zip")).is_err());
        assert!(library_type(Path::new("missing.SchLib")).is_err());
    }

    #[test]
    fn folder_scanning_finds_direct_libraries() {
        let folder = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("fixtures");
        let package = sources::resolve(&folder, &folder).unwrap();
        assert_eq!(
            package.schlib.unwrap().file_name().unwrap(),
            "ThreeSymbols.SchLib"
        );
        assert!(package.pcblib.is_none());
    }

    #[test]
    fn folder_scanning_warns_about_ambiguous_libraries() {
        let folder = std::env::temp_dir().join(format!(
            "alm-folder-test-{}-{}",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        std::fs::create_dir(&folder).unwrap();
        let fixture = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("fixtures")
            .join("ThreeSymbols.SchLib");
        let first = folder.join("first.SchLib");
        let second = folder.join("second.SchLib");
        std::fs::copy(&fixture, &first).unwrap();
        std::fs::copy(&fixture, &second).unwrap();
        let result = sources::resolve(&folder, &folder);
        std::fs::remove_file(first).unwrap();
        std::fs::remove_file(second).unwrap();
        std::fs::remove_dir(folder).unwrap();
        assert!(result
            .unwrap()
            .notes
            .iter()
            .any(|note| note.contains("Multiple")));
    }
}
