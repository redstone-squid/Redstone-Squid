use std::env;

fn main() {
    let target = env::var("TARGET").unwrap_or_else(|_| String::from("unknown"));
    println!("cargo:rustc-env=SQUID_BUILD_TARGET={target}");
}
